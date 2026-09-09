"""V5-Sentinel Reversal Manager -- main loop, now TWO components sharing
one magic number and one position slot. Design confirmed with the user
2026-09-06/07 for the original (STR) component (see htf_levels.py and
reversal_entry.py for its own data/detection layers), extended 2026-09-09
with a second (ICT) component (see reversal_ict.py for its own design --
OB-zone/NLB-NSB-Block-based, first of two planned entry methods, the
second, "candle identification strategy", still deferred).

Run with: python -m v5_sentinel.reversal_main

Summary of the STR component's own full rule set:
  - 9 HTF timeframes (D1, H8, H6, H4, H3, H2, H1, M30, M15), each
    independently classified STRONG/WEAK/TRAP via flip_state, with both
    trail lines exposed as individual SUPPORT/RESISTANCE levels
    (htf_levels.py). Pure copy_rates recompute, no bridge tie-breaker --
    confirmed 2026-09-07 (the recompute is trusted here, unlike M3's
    bridge-backed Trend Manager).
  - A level is ARMED the moment LIVE price (bid for support, ask for
    resistance) touches it, and stays armed across cycles until traded or
    its parent HTF's own character changes (reversal_entry.scan_touches).
  - Confirmation + entry (reversal_entry.find_signals): ONE trigger only
    now -- "3F", M3 ATR flip, privileged (fires even on the wrong side of
    the level, as long as that HTF's character hasn't yet changed),
    bridge-only, no copy_rates fallback. Two things were removed to get
    here, both same-day, both from real live incidents: M5/M3 candle-
    color triggers ("5C"/"3C") after H4 sitting in TRAP let them fire
    repeatedly against each other for ~55 minutes, ~150 trades, net -$62
    on that timeframe alone; and M1 flip ("1F", the gated Path 1
    trigger) per the user's explicit direction, "remove 1f logic
    completely and replace with strict 3F confirmation." If M3's bridge
    data is missing/stale, no signal is produced -- no fallback, no
    guess -- and bridge_flip.StaleAlertTracker fires an alert once that
    staleness has been SUSTAINED past 60s (not a momentary blip), once
    per staleness episode.
    The M3 far-line SL basis (bridge_flip.m3_far_line) was also switched
    to bridge-only the same day, for consistency -- M1's swing-low/high
    SL basis stays on copy_rates, unavoidably (the bridge publishes no
    OHLC at all, so there's no bridge alternative for real bar highs/
    lows).
  - One trade per flip: each level, once traded, is skipped until its
    parent HTF's own character changes (a fresh flip/trap-resolve there).
  - Position lifecycle (_process_signal, shared by BOTH components since
    2026-09-09), run once per qualifying signal per cycle -- STR's own
    signals scanned first, then ICT's, always against whatever position
    is ACTUALLY open at that moment (so an earlier signal's own action is
    visible to the next one this same cycle):
      no position               -> open fresh
      opposite direction        -> square off + reopen opposite
      same direction, full size -> ignore, mark traded, alert only
      same direction, partially cut -> refresh (square off + reopen full)
  - SL Manager, Trade Manager (70%/15% partial booking), and post-
    breakeven trailing all reuse Trend Manager's own classes UNCHANGED,
    just under RM's own magic number/state files -- confirmed 2026-09-07
    ("sl manager exactly works same...trade manager also works
    same...partial booking also takes place exactly same"). Trailing
    always follows M3's far line (bridge-only, see above) once past
    breakeven, regardless of which timeframe/trigger opened the trade --
    confirmed 2026-09-07. If M3's bridge is stale when a trailing update
    would otherwise fire, that cycle's SL update is skipped rather than
    guessed at.
  - Comments: "V5S-RM-STR-{HTF}/{trigger}" on entry (e.g.
    "V5S-RM-STR-H1/3F"), "-P1"/"-P2"/"-SQ"/"-RF" appended for partials/
    square-off/refresh. RM-STR (not just STR) is the literal prefix --
    briefly dropped the "RM-" 2026-09-07 to save space while the tag
    still carried a trailing timestamp, then added back the same day
    once that timestamp was removed elsewhere freed up plenty of room,
    since the later OB-based ICT sub-component needs "RM-ICT-" as its
    own distinct prefix to actually differentiate from this one (both
    share Reversal Manager's magic number space conceptually, but only
    the comment prefix tells them apart at a glance). _extract_tag()
    still accepts the older bare "V5S-STR-{tag}" shape too, for any
    position opened before this change whose entry comment is already
    frozen on the broker side.
  - Alerts: a qualifying-but-not-acted-on signal (the "ignore" case above)
    pushes to the existing @smcsecret_bot (TELEGRAM_BOT_TOKEN/
    TELEGRAM_CHAT_ID in .env, confirmed 2026-09-07) -- best-effort, never
    crashes the loop. Profit-milestone alerts were retired entirely
    2026-09-07 ("stop sending the profit alerts, now it is no longer
    needed") -- what used to be profit_alerts_watcher is now
    critical_alerts_watcher.py, a completely different thing (HTF
    support/resistance touch alerts on SecretTrader_Critical_Bot, not
    position-based at all any more).

Safety: V5S_RM_ENABLE_TRADING must be explicitly true in .env for any
order to actually be sent/modified/cancelled -- independent of Trend
Manager's own V5S_ENABLE_TRADING flag.
"""
from __future__ import annotations

import os
import time

import MetaTrader5 as mt5

from v5_sentinel import bridge, broker, heartbeat, htf_levels, reversal_entry, reversal_ict, sl_manager, trade_manager
from v5_sentinel.bridge_bar_flip import BridgeBarFlipTracker
from v5_sentinel.bridge_flip import StaleAlertTracker, m3_far_line
from v5_sentinel.critical_alerts_telegram import send_message as _telegram_send
from v5_sentinel.reversal_config import RMConfig, load_config

_DIR_LABEL = {1: "BUY", -1: "SELL"}
_COMPONENTS = ("STR", "ICT")  # the two Reversal Manager components sharing this magic number/position slot


def _tag(sig: "reversal_entry.ReversalSignal") -> str:
    return f"{htf_levels.TIMEFRAME_NAMES[sig.timeframe_minutes]}/{sig.trigger}"


def _ict_tag(sig: "reversal_ict.ICTSignal") -> str:
    return f"{sig.timeframe_name}/{sig.trigger}"


def _extract_tag(comment: str) -> str:
    """Mirrors main.py's _extract_tag() but for the "V5S-RM-{STR|ICT}-
    {tag}-..." shape -- used to carry an entry's own tag forward onto its
    later partial-booking comments. Joins everything after the fixed
    prefix (not just one part) so a dash-containing tag survives intact,
    same fix as main.py's own _extract_tag(). Also accepts the older
    "V5S-STR-{tag}" shape (pre-2026-09-07, before "RM-" was added back
    in) for backward compatibility with positions opened before this
    change -- their entry comment is frozen on the broker side and can
    never be rewritten, so their own later P1/P2 comments still need to
    resolve the right tag."""
    parts = comment.split("-") if comment else []
    if len(parts) >= 4 and parts[0] == "V5S" and parts[1] == "RM" and parts[2] in _COMPONENTS:
        return "-".join(parts[3:])
    if len(parts) >= 3 and parts[0] == "V5S" and parts[1] == "STR":
        return "-".join(parts[2:])
    return "UNK"


def _component_from_comment(comment: str) -> str:
    """Which of the two Reversal Manager components owns a given
    position, read back from its own entry comment -- 2026-09-09, added
    for RM-ICT (see reversal_ict.py's own docstring). Defaults "STR" for
    the older bare "V5S-STR-{tag}" shape or anything unrecognized,
    matching this system's original, only-ever-STR behavior before ICT
    existed."""
    parts = comment.split("-") if comment else []
    if len(parts) >= 3 and parts[0] == "V5S" and parts[1] == "RM" and parts[2] in _COMPONENTS:
        return parts[2]
    return "STR"


def _entry_comment(component: str, tag: str) -> str:
    return f"V5S-RM-{component}-{tag}"


def _action_comment(component: str, tag: str, action_code: str) -> str:
    return f"V5S-RM-{component}-{tag}-{action_code}"


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




def _open_position(cfg: RMConfig, component: str, direction: int, sl: float, tag: str, ref_desc: str) -> bool:
    """Returns True if the entry actually went through (filled, or
    enable_trading is False so it's decision-only and conceptually
    "accepted") -- False only on a genuine order rejection while live.
    2026-09-07 (found live, see htf_levels.py's own bugfix note): the
    caller must NOT mark eligibility consumed on a False return -- a
    failed order (e.g. retcode 10044 "session closed" right at market
    reopen) used to consume "one trade per flip" eligibility anyway,
    silently dropping a genuinely valid setup that never actually got a
    position. Generalized 2026-09-09 to serve both STR and ICT (see
    reversal_ict.py) -- component picks the log prefix and comment
    prefix, ref_desc is just a human-readable description of whatever
    triggered this (an HTF level's value for STR, a zone's own range for
    ICT) since the two components have no other field in common to print."""
    comment = _entry_comment(component, tag)
    print(f"[V5S-{component}-ENTRY] {_DIR_LABEL[direction]} ({tag}) {ref_desc} sl={sl:.3f}")
    if not cfg.enable_trading:
        print(f"[V5S-{component}-ENTRY] enable_trading is false -- decision only, no order sent")
        return True
    result = broker.send_market_order(cfg.symbol, direction, cfg.lots, sl, cfg.magic_number,
                                      cfg.deviation_points, comment)
    if not result.ok:
        print(f"[V5S-{component}-ENTRY] order_send failed: retcode={result.retcode} comment={result.comment}")
        return False
    print(f"[V5S-{component}-ENTRY] filled, ticket={result.ticket}")
    return True


def _close_position(cfg: RMConfig, component: str, position, action_label: str, tag: str, action_code: str) -> bool:
    print(f"[V5S-{component}-EXIT] closing #{position.ticket} ({action_label}), volume={position.volume}")
    if not cfg.enable_trading:
        print(f"[V5S-{component}-EXIT] enable_trading is false -- decision only, no order sent")
        return True
    result = broker.close_position(cfg.symbol, position, cfg.deviation_points,
                                   comment=_action_comment(component, tag, action_code))
    if not result.ok:
        print(f"[V5S-{component}-EXIT] close failed: retcode={result.retcode} comment={result.comment}")
        return False
    return True


def _run_sl_manager(cfg: RMConfig, mgr: sl_manager.SLManager, position) -> None:
    direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    bid, ask = broker.get_tick_price(cfg.symbol)
    current_price = bid if direction == 1 else ask
    far = m3_far_line(cfg.symbol, direction)
    if far is None:
        print("[V5S-STR-SL] M3 bridge stale/missing -- skipping SL update this cycle")
        return
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
    entry_comment = mgr.get_entry_comment(position.ticket) or ""
    component = _component_from_comment(entry_comment)
    entry_tag = _extract_tag(entry_comment)

    print(f"[V5S-{component}-TM] #{position.ticket} booking {label}: {volume} lots")
    if not cfg.enable_trading:
        print(f"[V5S-{component}-TM] enable_trading is false -- decision only, no close sent")
        return
    result = broker.close_position(cfg.symbol, position, cfg.deviation_points, volume=volume,
                                   comment=_action_comment(component, entry_tag, action_code))
    if not result.ok:
        print(f"[V5S-{component}-TM] partial close failed: retcode={result.retcode} comment={result.comment}")


def _process_signal(cfg: RMConfig, component: str, direction: int, sl: float, tag: str, ref_desc: str,
                    mark_traded, tm_mgr: trade_manager.TradeManager) -> None:
    """Acts on ONE signal against whatever position is ACTUALLY open right
    now (re-queried, so an earlier signal's own action this same cycle is
    visible here) -- shared by BOTH Reversal Manager components (STR and
    ICT, 2026-09-09), since they share the same magic number and the same
    one-position-at-a-time slot: "any leftovers, or whatever condition of
    trade, square off on opposite side valid setup and fire opposite
    side" (user's own words) is exactly this same branching regardless of
    which component's signal it is. mark_traded is a zero-arg callback
    (STR's own store.mark_traded(tf, direction) or ICT's own
    eligibility.mark_traded(zone_id), bound by the caller) so this
    function stays agnostic to which component's own eligibility scheme
    it's consuming. Only calls it once the entry actually went through
    (or, for the "already in a full-size trade" case, always -- no order
    is even attempted there) -- 2026-09-07, found live: a failed
    order_send used to consume eligibility anyway, silently dropping a
    genuinely valid setup that never got a position (see _open_position's
    own docstring)."""
    positions = broker.get_positions(cfg.symbol, cfg.magic_number)
    position = positions[0] if positions else None

    if position is None:
        if _open_position(cfg, component, direction, sl, tag, ref_desc):
            mark_traded()
        return

    pos_direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1

    if direction != pos_direction:
        if (_close_position(cfg, component, position, "SQOFF", tag, "SQ")
                and _open_position(cfg, component, direction, sl, tag, ref_desc)):
            mark_traded()
    elif tm_mgr.is_partially_cut(position.ticket):
        if (_close_position(cfg, component, position, "REFRESH", tag, "RF")
                and _open_position(cfg, component, direction, sl, tag, ref_desc)):
            mark_traded()
    else:
        mark_traded()
        msg = (f"[V5S-{component}] {tag} qualifies ({_DIR_LABEL[direction]}) but a full-size {_DIR_LABEL[pos_direction]} "
              f"position is already open on #{position.ticket} -- marked traded, no new entry")
        print(msg)
        _send_alert(msg)


def _check_stale(cfg: RMConfig, stale_tracker: StaleAlertTracker) -> None:
    """M3 only -- the sole remaining signal source (2026-09-07: M1 flip
    removed entirely, M5 candle removed earlier same day), nothing else
    to monitor here for this bot."""
    msg = stale_tracker.check(3, bridge.read_lines(cfg.symbol, 3) is not None)
    if msg is not None:
        print(msg)
        _send_alert(msg)


def run_once(cfg: RMConfig, sl_mgr: sl_manager.SLManager, tm_mgr: trade_manager.TradeManager,
            store: htf_levels.LevelEligibilityStore, ict_eligibility: reversal_ict.ICTEligibilityStore,
            tracker: BridgeBarFlipTracker, stale_tracker: StaleAlertTracker) -> None:
    htf_states = htf_levels.compute_all_htf_states(cfg.symbol)
    bid, ask = broker.get_tick_price(cfg.symbol)
    # Touch arming stays LIVE-tick (bid/ask against HTF levels) -- only
    # the M3 confirmation/entry trigger itself moved to bar-close-gated,
    # 2026-09-09: "live tick only for higher timeframe touch analysis,
    # decision and execution analysis is based on m3 which is bar close
    # analysis."
    reversal_entry.scan_touches(htf_states, store, bid, ask)
    _check_stale(cfg, stale_tracker)

    str_signals = reversal_entry.find_signals(cfg.symbol, htf_states, store, tracker, cfg.sl_buffer)
    # RM-ICT (second component, 2026-09-09) -- OB-zone (NLB/NSB Block)
    # based, same M3 bar-close FLIP gate, same tracker instance so both
    # components see the exact same fresh-event window. See
    # reversal_ict.py's own docstring for the full entry rule.
    ict_signals = reversal_ict.find_ict_signals(cfg.symbol, cfg.nlb_nsb_block_state_file, ict_eligibility,
                                                tracker, cfg.sl_buffer)

    positions = broker.get_positions(cfg.symbol, cfg.magic_number)
    sl_mgr.prune({p.ticket for p in positions})
    tm_mgr.prune({p.ticket for p in positions})

    # STR scanned first, then ICT -- fixed order so behaviour stays
    # deterministic when both happen to qualify the same cycle, same
    # reasoning reversal_entry.find_signals()'s own docstring gives for
    # its own fixed HTF scan order. Each runs against whatever position
    # is ACTUALLY open at that moment, so an earlier signal's own action
    # this same cycle is visible to the next one (_process_signal
    # re-queries every time).
    for sig in str_signals:
        _process_signal(cfg, "STR", sig.direction, sig.sl, _tag(sig), f"level={sig.level_value:.3f}",
                        lambda tf=sig.timeframe_minutes, d=sig.direction: store.mark_traded(tf, d), tm_mgr)
    for sig in ict_signals:
        _process_signal(cfg, "ICT", sig.direction, sig.sl, _ict_tag(sig),
                        f"zone=[{sig.zone_btm:.3f}-{sig.zone_top:.3f}]",
                        lambda zid=sig.zone_id: ict_eligibility.mark_traded(zid), tm_mgr)

    positions = broker.get_positions(cfg.symbol, cfg.magic_number)
    position = positions[0] if positions else None
    if position is not None:
        _run_sl_manager(cfg, sl_mgr, position)
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
    ict_eligibility = reversal_ict.ICTEligibilityStore(cfg.ict_eligibility_state_file)
    tracker = BridgeBarFlipTracker(cfg.bridge_bar_flip_state_file)
    stale_tracker = StaleAlertTracker()

    try:
        while True:
            try:
                run_once(cfg, sl_mgr, tm_mgr, store, ict_eligibility, tracker, stale_tracker)
            except Exception as exc:  # noqa: BLE001 -- keep the loop alive, log and continue
                print(f"[V5S-STR] cycle error: {exc!r}")
            # See heartbeat.py's own docstring -- proves the loop itself
            # is alive, regardless of whether this cycle raised. Added
            # 2026-09-08 after this exact process went silently inert for
            # over 25 hours while still showing as a running OS process.
            heartbeat.write(cfg.heartbeat_file)
            time.sleep(cfg.poll_seconds)
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
