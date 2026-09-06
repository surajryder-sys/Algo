"""V5-Sentinel STR Reversal Manager -- main loop. Design confirmed with
the user 2026-09-06/07 (see htf_levels.py and reversal_entry.py for the
data/detection layers this wires together).

Run with: python -m v5_sentinel.reversal_main

Summary of the full rule set implemented here:
  - 9 HTF timeframes (D1, H8, H6, H4, H3, H2, H1, M30, M15), each
    independently classified STRONG/WEAK/TRAP via flip_state, with both
    trail lines exposed as individual SUPPORT/RESISTANCE levels
    (htf_levels.py). Pure copy_rates recompute, no bridge tie-breaker --
    confirmed 2026-09-07 (the recompute is trusted here, unlike M3's
    bridge-backed Trend Manager).
  - A level is ARMED the moment LIVE price (bid for support, ask for
    resistance) touches it, and stays armed across cycles until traded or
    its parent HTF's own character changes (reversal_entry.scan_touches).
  - Confirmation + entry (reversal_entry.find_signals): M5/M3 bullish/
    bearish candle checks are closed-bar copy_rates; M3/M1 flip checks
    read the LIVE BRIDGE ONLY (bridge_flip.py), no copy_rates fallback --
    changed 2026-09-07 at the user's request, since the bridge's own
    continuously-running lines are considered more reliable for LTF flip
    detection than an independent recompute (same reasoning as Trend
    Manager's own M3 bridge tie-breaker, just as the sole source here
    instead of a disagreement-only override). Path 1 gated (candle
    checks vs. that bar's own close; M1 flip vs. live price) or Path 2
    privileged (M3 ATR flip, fires even on the wrong side of the level,
    as long as that HTF's character hasn't yet changed).
  - One trade per flip: each level, once traded, is skipped until its
    parent HTF's own character changes (a fresh flip/trap-resolve there).
  - Position lifecycle, run once per qualifying signal per cycle (in
    scan order), always against whatever position is ACTUALLY open at
    that moment (so an earlier signal's own action is visible to the
    next one this same cycle):
      no position               -> open fresh
      opposite direction        -> square off + reopen opposite
      same direction, full size -> ignore, mark traded, alert only
      same direction, partially cut -> refresh (square off + reopen full)
  - SL Manager, Trade Manager (70%/15% partial booking), and post-
    breakeven trailing all reuse Trend Manager's own classes UNCHANGED,
    just under RM's own magic number/state files -- confirmed 2026-09-07
    ("sl manager exactly works same...trade manager also works
    same...partial booking also takes place exactly same"). Trailing
    always follows M3's far line once past breakeven, regardless of
    which timeframe/trigger opened the trade -- confirmed 2026-09-07.
  - Comments: "V5S-STR-{HTF}/{trigger}" on entry (e.g. "V5S-STR-H1/3C"),
    "-P1"/"-P2"/"-SQ"/"-RF" appended for partials/square-off/refresh.
    STR (not RM) is the literal prefix used, to distinguish this
    Structure-based sub-component from the later OB-based ICT one
    (confirmed 2026-09-07) -- both will share Reversal Manager's magic
    number space conceptually but this file only ever writes STR trades.
  - Alerts: a qualifying-but-not-acted-on signal (the "ignore" case above)
    pushes to the existing @smcsecret_bot (TELEGRAM_BOT_TOKEN/
    TELEGRAM_CHAT_ID in .env, confirmed 2026-09-07) -- best-effort, never
    crashes the loop. Profit alerts are NOT sent from here; they go
    through the existing profit_alerts_watcher, which also needs to be
    told to watch this magic number (separate change).

Safety: V5S_RM_ENABLE_TRADING must be explicitly true in .env for any
order to actually be sent/modified/cancelled -- independent of Trend
Manager's own V5S_ENABLE_TRADING flag.
"""
from __future__ import annotations

import os
import time

import MetaTrader5 as mt5

from v5_sentinel import broker, flip_state, htf_levels, rates, reversal_entry, sl_manager, trade_manager
from v5_sentinel.bridge_flip import BridgeFlipState
from v5_sentinel.profit_alerts_telegram import send_message as _telegram_send
from v5_sentinel.reversal_config import RMConfig, load_config

_M1_MINUTES, _M3_MINUTES, _M5_MINUTES = 1, 3, 5
_DIR_LABEL = {1: "BUY", -1: "SELL"}


def _tag(sig: "reversal_entry.ReversalSignal") -> str:
    return f"{htf_levels.TIMEFRAME_NAMES[sig.timeframe_minutes]}/{sig.trigger}"


def _extract_tag(comment: str) -> str:
    """Mirrors main.py's _extract_tag() but for the "V5S-STR-{tag}-..."
    shape -- used to carry an entry's own tag forward onto its later
    partial-booking comments."""
    parts = comment.split("-") if comment else []
    if len(parts) >= 3 and parts[0] == "V5S" and parts[1] == "STR":
        return parts[2]
    return "UNK"


def _entry_comment(tag: str) -> str:
    return f"V5S-STR-{tag}"


def _action_comment(tag: str, action_code: str) -> str:
    return f"V5S-STR-{tag}-{action_code}"


def _send_alert(text: str) -> None:
    """Best-effort push to @smcsecret_bot -- never raises, a Telegram
    outage should never take the trading loop down with it."""
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print(f"[V5S-STR-ALERT] (no bot configured) {text}")
        return
    try:
        _telegram_send(token, chat_id, text)
    except Exception as exc:  # noqa: BLE001 -- alerting must never break the loop
        print(f"[V5S-STR-ALERT] send failed: {exc!r} -- message was: {text}")


def _far_line_for(direction: int, m3_series: rates.TrailSeries) -> float:
    far, _near = flip_state.far_near_line(direction, m3_series.trail1[-1], m3_series.trail2[-1])
    return far


def _open_position(cfg: RMConfig, sig: "reversal_entry.ReversalSignal", tag: str) -> None:
    comment = _entry_comment(tag)
    print(f"[V5S-STR-ENTRY] {_DIR_LABEL[sig.direction]} ({tag}) level={sig.level_value:.3f} sl={sig.sl:.3f}")
    if not cfg.enable_trading:
        print("[V5S-STR-ENTRY] enable_trading is false -- decision only, no order sent")
        return
    result = broker.send_market_order(cfg.symbol, sig.direction, cfg.lots, sig.sl, cfg.magic_number,
                                      cfg.deviation_points, comment)
    if not result.ok:
        print(f"[V5S-STR-ENTRY] order_send failed: retcode={result.retcode} comment={result.comment}")
    else:
        print(f"[V5S-STR-ENTRY] filled, ticket={result.ticket}")


def _close_position(cfg: RMConfig, position, action_label: str, tag: str, action_code: str) -> bool:
    print(f"[V5S-STR-EXIT] closing #{position.ticket} ({action_label}), volume={position.volume}")
    if not cfg.enable_trading:
        print("[V5S-STR-EXIT] enable_trading is false -- decision only, no order sent")
        return True
    result = broker.close_position(cfg.symbol, position, cfg.deviation_points,
                                   comment=_action_comment(tag, action_code))
    if not result.ok:
        print(f"[V5S-STR-EXIT] close failed: retcode={result.retcode} comment={result.comment}")
        return False
    return True


def _run_sl_manager(cfg: RMConfig, mgr: sl_manager.SLManager, position, m3_series: rates.TrailSeries) -> None:
    direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    bid, ask = broker.get_tick_price(cfg.symbol)
    current_price = bid if direction == 1 else ask
    far = _far_line_for(direction, m3_series)
    current_sl = position.sl if position.sl else None

    proposed = mgr.compute(position.ticket, direction, position.price_open, current_price, current_sl, far)
    if proposed is None:
        return
    print(f"[V5S-STR-SL] #{position.ticket} -> {proposed:.3f}")
    if not cfg.enable_trading:
        print("[V5S-STR-SL] enable_trading is false -- decision only, no modify sent")
        return
    result = broker.modify_position_sl(cfg.symbol, position.ticket, proposed, tp=position.tp)
    if result.ok:
        mgr.confirm_applied(position.ticket, proposed)
    else:
        print(f"[V5S-STR-SL] modify failed: retcode={result.retcode} comment={result.comment}")


def _run_trade_manager(cfg: RMConfig, mgr: trade_manager.TradeManager, position) -> None:
    direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    bid, ask = broker.get_tick_price(cfg.symbol)
    current_price = bid if direction == 1 else ask
    has_tp = broker.has_manual_tp(position)

    symbol_info = mt5.symbol_info(cfg.symbol)
    volume_step = symbol_info.volume_step if symbol_info is not None else 0.01

    outcome = mgr.evaluate(position.ticket, direction, position.price_open, current_price,
                           position.volume, has_tp, volume_step, entry_comment=position.comment)
    if outcome is None:
        return
    volume, label = outcome
    action_code = "P1" if label == "partial1" else "P2"
    entry_tag = _extract_tag(mgr.get_entry_comment(position.ticket) or "")

    print(f"[V5S-STR-TM] #{position.ticket} booking {label}: {volume} lots")
    if not cfg.enable_trading:
        print("[V5S-STR-TM] enable_trading is false -- decision only, no close sent")
        return
    result = broker.close_position(cfg.symbol, position, cfg.deviation_points, volume=volume,
                                   comment=_action_comment(entry_tag, action_code))
    if not result.ok:
        print(f"[V5S-STR-TM] partial close failed: retcode={result.retcode} comment={result.comment}")


def _process_signal(cfg: RMConfig, sig: "reversal_entry.ReversalSignal", store: htf_levels.LevelEligibilityStore,
                    tm_mgr: trade_manager.TradeManager) -> None:
    """Acts on ONE signal against whatever position is ACTUALLY open right
    now (re-queried, so an earlier signal's own action this same cycle is
    visible here) -- always ends by marking the level traded, and either
    performs a real trade action or sends an alert, never a silent no-op."""
    tag = _tag(sig)
    positions = broker.get_positions(cfg.symbol, cfg.magic_number)
    position = positions[0] if positions else None

    if position is None:
        _open_position(cfg, sig, tag)
        store.mark_traded(sig.timeframe_minutes, sig.direction)
        return

    pos_direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1

    if sig.direction != pos_direction:
        if _close_position(cfg, position, "SQOFF", tag, "SQ"):
            _open_position(cfg, sig, tag)
        store.mark_traded(sig.timeframe_minutes, sig.direction)
    elif tm_mgr.is_partially_cut(position.ticket):
        if _close_position(cfg, position, "REFRESH", tag, "RF"):
            _open_position(cfg, sig, tag)
        store.mark_traded(sig.timeframe_minutes, sig.direction)
    else:
        store.mark_traded(sig.timeframe_minutes, sig.direction)
        msg = (f"[V5S-STR] {tag} qualifies ({_DIR_LABEL[sig.direction]}) but a full-size {_DIR_LABEL[pos_direction]} "
              f"position is already open on #{position.ticket} -- marked traded, no new entry")
        print(msg)
        _send_alert(msg)


def run_once(cfg: RMConfig, sl_mgr: sl_manager.SLManager, tm_mgr: trade_manager.TradeManager,
            store: htf_levels.LevelEligibilityStore, bridge_flip: BridgeFlipState) -> None:
    htf_states = htf_levels.compute_all_htf_states(cfg.symbol)
    bid, ask = broker.get_tick_price(cfg.symbol)
    reversal_entry.scan_touches(htf_states, store, bid, ask)

    m5_series = rates.read_trail_series(cfg.symbol, _M5_MINUTES)
    m3_series = rates.read_trail_series(cfg.symbol, _M3_MINUTES)
    m1_series = rates.read_trail_series(cfg.symbol, _M1_MINUTES)
    if m3_series is None:
        print("[V5S-STR] waiting for enough M3 bar history")
        return

    signals = reversal_entry.find_signals(cfg.symbol, htf_states, store, bridge_flip, bid, ask,
                                          m5_series, m3_series, m1_series,
                                          cfg.sl_buffer, cfg.swing_lookback)

    positions = broker.get_positions(cfg.symbol, cfg.magic_number)
    sl_mgr.prune({p.ticket for p in positions})
    tm_mgr.prune({p.ticket for p in positions})

    for sig in signals:
        _process_signal(cfg, sig, store, tm_mgr)

    positions = broker.get_positions(cfg.symbol, cfg.magic_number)
    position = positions[0] if positions else None
    if position is not None:
        _run_sl_manager(cfg, sl_mgr, position, m3_series)
        _run_trade_manager(cfg, tm_mgr, position)


def main() -> None:
    cfg = load_config()
    print(f"[V5S-STR] starting -- symbol={cfg.symbol} magic={cfg.magic_number} "
          f"enable_trading={cfg.enable_trading} poll={cfg.poll_seconds}s")

    broker.connect(cfg)
    sl_mgr = sl_manager.SLManager(cfg.sl_state_file, cfg.breakeven_trigger_points, cfg.sl_buffer)
    tm_mgr = trade_manager.TradeManager(cfg.state_file, cfg.partial1_trigger_points, cfg.partial1_fraction,
                                        cfg.partial2_trigger_points, cfg.partial2_fraction)
    store = htf_levels.LevelEligibilityStore(cfg.levels_state_file)
    bridge_flip = BridgeFlipState(cfg.bridge_flip_state_file)

    try:
        while True:
            try:
                run_once(cfg, sl_mgr, tm_mgr, store, bridge_flip)
            except Exception as exc:  # noqa: BLE001 -- keep the loop alive, log and continue
                print(f"[V5S-STR] cycle error: {exc!r}")
            time.sleep(cfg.poll_seconds)
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
