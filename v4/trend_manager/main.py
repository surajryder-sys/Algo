"""V4 Trend Manager -- M1 execution poll loop.

Run with: python -m v4.trend_manager.main

Wires m1_execution.evaluate_entry to REAL, LIVE data:
  - Dual-ATR structure: MT5-native only
    (mql5/SurajBot_ATRTrail_..._DUAL.mq5's bridge,
    v4.bridge.reader.read_atr_dual), M1 only. TradingView's own dual-ATR
    flip (v4.bridge.tv_atr.read_tv_structure) was wired in as a second
    race source earlier the same day, then explicitly dropped again --
    "tradingview flip, lets ignore on atr, lets only see MT5, for m1
    execution" (2026-08-28) -- so this now passes tv_structure=None into
    evaluate_entry, which already handles that (MT5 alone drives the
    decision; see that function's own docstring). read_tv_structure stays
    unused-but-available here rather than deleted, in case this gets
    revisited.
  - Opposing OB zones for the 5-point edge-gap filter:
    mql5/OB_Zone_Bridge_Lite.mq5's bridge (v4.bridge.reader.read_zone_lite),
    M1's own bull/bear zones -- MT5-native, not the TV scraper (M5/M3
    parent-bias gating was considered and explicitly dropped the same
    day -- "no need of m3 and m5 now" -- this is M1-only end to end).
  - Previous candle close and current price: MT5 directly
    (v4.trend_manager.broker), not any bridge file. SL is anchored to
    MT5's own trail values specifically (see m1_execution.py).
  - Wider H4/H2/H1/M30/M15/M5 buffer zones, folded into the SAME 5-point
    edge-gap filter as M1's own zones (never baseline -- corrected
    2026-08-28 after briefly reintroducing baseline for these by mistake):
    the TV scraper (v4.bridge.tv_zones), per explicit instruction
    2026-08-28 ("ob zones to watch only H4, H2, H1, M30, M15, M5") -- the
    one place this module still depends on TradingView data, since only
    it publishes that many timeframes at once; MT5's own bridges only
    cover M5/M3/M1.

Safety: V4_ENABLE_TRADING must be explicitly set to true in .env for any
order to actually be sent -- left unset (default false), every resolved
decision is printed but nothing touches the account. Matches every other
bot in this repo (see CLAUDE.md).
"""
from __future__ import annotations

import datetime
import time

from v4.bridge.reader import read_atr_dual, read_zone_lite
from v4.bridge.tv_zones import read_all_zones
# v4.bridge.tv_atr.read_tv_structure intentionally not imported -- MT5-only
# for M1 execution now, see this module's own docstring.
from v4.trend_manager import broker
from v4.trend_manager.config import load_config
from v4.trend_manager.exit_manager import ExitManagerState, evaluate_exit_actions
from v4.trend_manager.m1_execution import V4ExecutionState, evaluate_entry


_SOURCE_CODE = {"mt5": "MT", "tv": "TV", "both": "BT"}
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _log(msg: str) -> None:
    """IST-timestamped -- added 2026-08-28 after repeatedly needing to
    manually reconstruct WHEN something happened from bare event_times.
    Every poll logs something now, not just the interesting ones -- "it
    should have the problem written, why the trade was not executed, and
    if executed, the reason for execution as well.\""""
    ts = datetime.datetime.now(tz=_IST).strftime("%H:%M:%S")
    print(f"[v4.trend_manager {ts} IST] {msg}")

# Buffers only ever come from these six -- M3/M1 are never buffers, per
# explicit instruction (see m1_execution.py's own docstring).
_BUFFER_TIMEFRAMES = ("H4", "H2", "H1", "M30", "M15", "M5")


def _buffer_zones(symbol: str, structure: str) -> list:
    """All opposing-direction zones across the six buffer timeframes --
    bear zones (no_long) when the flip is bullish (STRONG), bull zones
    (no_short) when it's bearish (WEAK). Returns [] (fails open, not
    closed) if the scraper's zone file is missing/mid-write this poll --
    same transient-failure tolerance as every other bridge read here."""
    zones = read_all_zones(symbol)
    if zones is None:
        return []
    out = []
    for label in _BUFFER_TIMEFRAMES:
        tf = zones.get(label)
        if tf is None:
            continue
        out.extend(tf.bear if structure == "STRONG" else tf.bull)
    return out


def _comment(decision) -> str:
    """"V4S" = V4 Sentinel. No L/S direction field -- the position's own
    buy/sell type already shows that, no need to duplicate it in the
    comment text. e.g. "V4S-M1-M1-MT-1787928779" (24 chars) -- well under
    MT5's real 31-character comment limit."""
    return f"V4S-M1-M1-{_SOURCE_CODE[decision.source]}-{int(time.time())}"


def _manage_open_position(cfg, exit_state: ExitManagerState) -> None:
    """Breakeven + tiered profit-booking for a currently open V4 position,
    per the user's explicit rule 2026-08-28 -- runs independently of the
    entry logic below (no ATR/OB bridge data needed, just the position's
    own real numbers from MT5). No-op if no position is open right now --
    exit_manager.py's own state naturally resets the next time a NEW
    ticket shows up, so nothing needs clearing here on a flat account."""
    position = broker.get_position(cfg)
    if position is None:
        return

    direction = "buy" if position.type == 0 else "sell"  # ORDER_TYPE_BUY == 0
    sl_update, closes = evaluate_exit_actions(
        exit_state, position.ticket, direction, position.price_open,
        position.price_current, position.sl, cfg.lot_size,
    )

    if sl_update is not None:
        if cfg.enable_trading:
            r = broker.modify_sl(cfg, position.ticket, sl_update.new_sl)
            _log(f"EXIT MANAGER: SL -> {sl_update.new_sl:.3f} (breakeven) on ticket "
                 f"{position.ticket} -- result={r}")
        else:
            _log(f"EXIT MANAGER (DRY-RUN): would move SL -> {sl_update.new_sl:.3f} "
                 f"(breakeven) on ticket {position.ticket}")

    for close in closes:
        comment = f"V4S-EXIT-{close.tier.upper()}-{int(time.time())}"
        if cfg.enable_trading:
            r = broker.partial_close(cfg, position.ticket, direction, close.volume, comment)
            _log(f"EXIT MANAGER: partial close {close.tier} vol={close.volume} on ticket "
                 f"{position.ticket} -- result={r}")
        else:
            _log(f"EXIT MANAGER (DRY-RUN): would partial close {close.tier} "
                 f"vol={close.volume} on ticket {position.ticket}")


def run_once(cfg, state: V4ExecutionState, exit_state: ExitManagerState) -> None:
    # Reconcile tracked state against the REAL account first, every poll --
    # fixes a real bug confirmed live 2026-08-28: a position closed via SL
    # left active_direction stuck forever after, silently swallowing the
    # next genuine same-direction flip (see V4ExecutionState.reconcile's
    # own docstring for the full incident).
    had_position = broker.has_open_position(cfg)
    state.reconcile(had_position)

    if had_position:
        _manage_open_position(cfg, exit_state)

    mt5_atr = read_atr_dual(cfg.symbol, 1)
    if mt5_atr is None or mt5_atr.is_stale():
        _log(f"M1 MT5 ATR bridge missing/stale -- skipping this poll (position_open={had_position})")
        return

    # MT5-only for M1 execution -- TradingView's own flip is deliberately
    # not read here anymore (see this module's own docstring).
    tv_structure = None

    ob = read_zone_lite(cfg.symbol, 1)
    opposing_zones = []
    if ob is not None and not ob.is_stale():
        opposing_zones = ob.bear if mt5_atr.structure == "STRONG" else ob.bull
    else:
        _log("M1 OB bridge missing/stale -- proceeding with no M1 edge-gap zones known")

    previous_close = broker.find_previous_candle_close(cfg.symbol, mt5_atr.structure_event_time)
    if previous_close is None:
        _log(f"could not find the flip candle (structure_event_time={mt5_atr.structure_event_time}) "
             f"in MT5 history -- skipping this poll")
        return

    current_price = broker.get_mid_price(cfg.symbol)
    buffer_zones = _buffer_zones(cfg.symbol, mt5_atr.structure)

    result = evaluate_entry(state, mt5_atr, tv_structure, previous_close, current_price,
                             opposing_zones, buffer_zones)
    # ALWAYS logged -- every poll, every outcome, not just the interesting
    # ones. This line alone should answer "why didn't/did the trade fire"
    # without needing a separate investigation.
    _log(f"mt5={mt5_atr.structure} price={current_price:.3f} flip_close={previous_close:.3f} "
         f"position_open={had_position} -> {result.reason}")

    if result.decision is None:
        return
    decision = result.decision

    comment = _comment(decision)
    if not cfg.enable_trading:
        _log(f"ENTRY SIGNAL (DRY-RUN, V4_ENABLE_TRADING is not true -- nothing sent): "
             f"{decision.direction.upper()} {cfg.symbol} | initial_sl={decision.initial_sl:.3f} "
             f"(far={decision.far_line}) | source={decision.source} | comment={comment!r}")
        return

    order_result = broker.send_market_order(cfg, decision.direction, decision.initial_sl, comment)
    _log(f"ORDER SENT: {decision.direction.upper()} {cfg.symbol} "
         f"lot={cfg.lot_size} sl={decision.initial_sl:.3f} comment={comment!r} -- result={order_result}")
    if order_result is not None and order_result.retcode == 10009:  # TRADE_RETCODE_DONE
        state.mark_position_opened()


def main() -> None:
    cfg = load_config()
    broker.connect(cfg)
    state = V4ExecutionState(cfg.m1_execution_state_file)
    exit_state = ExitManagerState(cfg.exit_manager_state_file)

    mode = "LIVE (real orders will be sent)" if cfg.enable_trading else "DRY-RUN (signals printed only)"
    _log(f"connected -- watching {cfg.symbol} M1, polling every {cfg.poll_seconds}s -- {mode}")

    try:
        while True:
            run_once(cfg, state, exit_state)
            time.sleep(cfg.poll_seconds)
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
