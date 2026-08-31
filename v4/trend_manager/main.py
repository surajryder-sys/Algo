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
  - Previous candle close and current price: MT5 directly
    (v4.trend_manager.broker), not any bridge file. SL is anchored to
    MT5's own trail values specifically (see m1_execution.py).
  - MT5-native M2 and M4 charts/ATR reads: briefly added 2026-08-31
    (read/store-only, never wired into any decision) alongside two new
    charts, then REMOVED the same day once the user closed those charts
    again over candle-lag concerns from running too many MT5 charts at
    once -- see v4/bridge/reader.py's EXECUTION_TIMEFRAMES comment.

OB zone data (M1's own via mql5/OB_Zone_Bridge_Lite.mq5, the same bridge
read again at M5, and the wider TV-scraper H4-M5 buffer timeframes) is
still read and logged every poll for support/resistance visibility, but
is NO LONGER passed into evaluate_entry or used to gate/block entries in
any way -- the entire zone-based edge-gap filter was REMOVED 2026-08-31,
user's explicit request: "remove zone block technique entirely, i wanna
deploy a new technique for blocking orders... have the zone data to
identify resistance and support of order blocks, but remove it from
execution logic completely." A fresh, non-stale flip now fires
unconditionally (see m1_execution.py's own docstring) until a
replacement blocking technique is built and wired in here.

Safety: V4_ENABLE_TRADING must be explicitly set to true in .env for any
order to actually be sent -- left unset (default false), every resolved
decision is printed but nothing touches the account. Matches every other
bot in this repo (see CLAUDE.md).
"""
from __future__ import annotations

import datetime
import time
from dataclasses import dataclass
from typing import Literal, Optional

from v4.bridge.reader import read_atr_dual, read_zone_lite
from v4.bridge.tv_zones import read_all_zones
# v4.bridge.tv_atr.read_tv_structure intentionally not imported -- MT5-only
# for M1 execution now, see this module's own docstring.
from v4.trend_manager import broker
from v4.trend_manager.config import load_config
from v4.trend_manager.exit_manager import ExitManagerState, evaluate_exit_actions
from v4.trend_manager.m1_execution import V4ExecutionState, evaluate_entry
from v4.trend_manager.trap_watch import TrapWatchState, evaluate_trap_watch, is_direction_blocked


_SOURCE_CODE = {"mt5": "MT", "tv": "TV", "both": "BT"}
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


@dataclass
class LabeledZone:
    """Wraps a raw zone (either v4.bridge.reader.Zone -- M1's own -- or
    v4.bridge.tv_zones.Zone -- a buffer timeframe) with which timeframe it
    came from, since neither underlying Zone class carries that itself.
    Added 2026-08-31, extended the same day to carry virgin/start_time
    too, and again to carry `side` (bull/bear) once the zone-based
    edge-gap filter was removed entirely and this became pure
    support/resistance INFORMATION rather than a directional "opposing
    zone" input to a blocking decision -- see this module's own top
    docstring."""
    high: float
    low: float
    label: str
    virgin: bool
    start_time: int
    side: Literal["bull", "bear"]


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


def _buffer_zones(symbol: str) -> list[LabeledZone]:
    """EVERY bull+bear zone across the six buffer timeframes, tagged with
    both `label` (which timeframe) and `side` (bull/bear) -- pure
    support/resistance information now, no longer filtered to one
    "opposing" side by structure direction (that framing only made sense
    for the zone-block technique removed 2026-08-31; see this module's
    own top docstring). Returns [] (fails open, not closed) if the
    scraper's zone file is missing/mid-write this poll -- same
    transient-failure tolerance as every other bridge read here."""
    zones = read_all_zones(symbol)
    if zones is None:
        return []
    out: list[LabeledZone] = []
    for label in _BUFFER_TIMEFRAMES:
        tf = zones.get(label)
        if tf is None:
            continue
        out.extend(LabeledZone(high=z.high, low=z.low, label=label, virgin=z.virgin,
                                start_time=z.start_time, side="bull") for z in tf.bull)
        out.extend(LabeledZone(high=z.high, low=z.low, label=label, virgin=z.virgin,
                                start_time=z.start_time, side="bear") for z in tf.bear)
    return out


def _nearest_support_resistance(
    zones: list[LabeledZone], current_price: float,
) -> tuple[Optional[LabeledZone], Optional[LabeledZone]]:
    """(nearest_support, nearest_resistance) -- INFORMATIONAL ONLY, not
    used for any entry decision (zone-block technique removed 2026-08-31,
    "have the zone data to identify resistance and support of order
    blocks, but remove it from execution logic completely"). Support =
    the bull zone whose high sits closest below current price;
    resistance = the bear zone whose low sits closest above it."""
    supports = [z for z in zones if z.side == "bull" and z.high < current_price]
    resistances = [z for z in zones if z.side == "bear" and z.low > current_price]
    nearest_support = max(supports, key=lambda z: z.high) if supports else None
    nearest_resistance = min(resistances, key=lambda z: z.low) if resistances else None
    return nearest_support, nearest_resistance


def _comment(decision) -> str:
    """"V4S" = V4 Sentinel. No L/S direction field -- the position's own
    buy/sell type already shows that, no need to duplicate it in the
    comment text. e.g. "V4S-M1-M1-MT-1787928779" (24 chars) -- well under
    MT5's real 31-character comment limit."""
    return f"V4S-M1-M1-{_SOURCE_CODE[decision.source]}-{int(time.time())}"


def _manage_open_position(cfg, exit_state: ExitManagerState) -> None:
    """Breakeven + tiered profit-booking for EVERY currently open V4
    position, per the user's explicit rule 2026-08-28 -- runs
    independently of the entry logic below (no ATR/OB bridge data
    needed, just each position's own real numbers from MT5).

    Rewritten 2026-08-31 to loop over broker.get_all_positions() instead
    of the single get_position() -- confirmed live this account can
    genuinely hold more than one open ticket at once (a tier2 leftover
    plus a freshly-fired same-direction position), and the old
    single-ticket version only ever managed whichever ONE
    positions_get() happened to return first -- the other ticket got NO
    breakeven/tier tracking at all, silently. Also prunes
    ExitManagerState of any ticket no longer open, so closed positions
    don't accumulate in that file forever."""
    positions = broker.get_all_positions(cfg)
    exit_state.prune({p.ticket for p in positions})
    if not positions:
        return

    for position in positions:
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


def run_once(cfg, state: V4ExecutionState, exit_state: ExitManagerState, trap_state: TrapWatchState) -> None:
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

    # M2/M4 ATR store-only reads (briefly added 2026-08-31 alongside two
    # new charts) REMOVED the same day once the user closed those charts
    # again -- "closed m4 and m2 charts as there is a candle lag if i
    # open too many charts on mt5" -- see EXECUTION_TIMEFRAMES's own
    # comment in v4/bridge/reader.py.

    # MT5-only for M1 execution -- TradingView's own flip is deliberately
    # not read here anymore (see this module's own docstring).
    tv_structure = None

    # OB zone data below is INFORMATIONAL ONLY from here on -- support/
    # resistance visibility, never passed into evaluate_entry. The
    # zone-based edge-gap filter was removed entirely 2026-08-31, see
    # this module's own top docstring.
    ob = read_zone_lite(cfg.symbol, 1)
    m1_zones: list[LabeledZone] = []
    if ob is not None and not ob.is_stale():
        m1_zones = [LabeledZone(high=z.high, low=z.low, label="M1", virgin=z.virgin,
                                 start_time=z.start_time, side="bull") for z in ob.bull]
        m1_zones += [LabeledZone(high=z.high, low=z.low, label="M1", virgin=z.virgin,
                                  start_time=z.start_time, side="bear") for z in ob.bear]
    else:
        _log("M1 OB bridge missing/stale -- no M1 zone data this poll")

    ob_m5 = read_zone_lite(cfg.symbol, 5)
    m5_mt5_zones: list[LabeledZone] = []
    if ob_m5 is not None and not ob_m5.is_stale():
        m5_mt5_zones = [LabeledZone(high=z.high, low=z.low, label="M5(MT5)", virgin=z.virgin,
                                     start_time=z.start_time, side="bull") for z in ob_m5.bull]
        m5_mt5_zones += [LabeledZone(high=z.high, low=z.low, label="M5(MT5)", virgin=z.virgin,
                                      start_time=z.start_time, side="bear") for z in ob_m5.bear]
    else:
        _log("MT5-native M5 OB bridge missing/stale -- no M5(MT5) zone data this poll")

    previous_close = broker.find_previous_candle_close(cfg.symbol, mt5_atr.structure_event_time)
    if previous_close is None:
        _log(f"could not find the flip candle (structure_event_time={mt5_atr.structure_event_time}) "
             f"in MT5 history -- skipping this poll")
        return

    current_price = broker.get_mid_price(cfg.symbol)
    all_zones = m1_zones + m5_mt5_zones + _buffer_zones(cfg.symbol)
    support, resistance = _nearest_support_resistance(all_zones, current_price)
    support_str = f"{support.high:.3f} ({support.label})" if support is not None else "none known"
    resistance_str = f"{resistance.low:.3f} ({resistance.label})" if resistance is not None else "none known"
    _log(f"S/R (info only, not used for entries): support={support_str} resistance={resistance_str}")

    # New blocking technique -- replaces the removed OB-zone edge-gap
    # filter, per the user's explicit design 2026-08-31/09-01 (see
    # trap_watch.py's own docstring for the full mechanism: touch a
    # M3/M5 trail line, wait for M1's own reaction, close the trade and
    # trap that price level on confirmed rejection).
    trap_result = evaluate_trap_watch(trap_state, cfg.symbol, current_price, mt5_atr)
    for rejection in trap_result.rejections:
        _log(f"TRAP WATCH: {rejection.side} rejection confirmed at {rejection.level:.3f} "
             f"({rejection.line_key}) -- trap zone set for [{rejection.close_direction}]")
        existing = broker.position_direction(cfg) if had_position else None
        if existing == rejection.close_direction:
            if cfg.enable_trading:
                close_results = broker.close_position(cfg, f"V4S-TRAP-CLOSE-{int(time.time())}")
                _log(f"TRAP WATCH: closed {len(close_results)} {rejection.close_direction} "
                     f"position(s) -- results={close_results}")
            else:
                _log(f"TRAP WATCH (DRY-RUN): would close existing {rejection.close_direction} position(s)")

    result = evaluate_entry(state, mt5_atr, tv_structure, previous_close, current_price)
    decision = result.decision
    reason = result.reason
    if decision is not None:
        block_reason = is_direction_blocked(trap_result, decision.direction, previous_close)
        if block_reason is not None:
            decision = None
            reason = block_reason

    # ALWAYS logged -- every poll, every outcome, not just the interesting
    # ones. This line alone should answer "why didn't/did the trade fire"
    # without needing a separate investigation.
    _log(f"mt5={mt5_atr.structure} price={current_price:.3f} flip_close={previous_close:.3f} "
         f"position_open={had_position} -> {reason}")

    if decision is None:
        return

    # Confirmed live, 2026-08-31: this account is RETAIL_HEDGING, not
    # netting (same bug/fix as crypto_trend_manager's own _fire(), found
    # one day earlier) -- a real BUY and a real SELL sat open
    # simultaneously for 23 minutes because a valid opposite-direction
    # signal just opened a second, separate hedged position instead of
    # reversing the existing one. existing_direction is read BEFORE this
    # poll's own actions (had_position/state.reconcile already ran above).
    existing_direction = broker.position_direction(cfg) if had_position else None
    reversing = existing_direction is not None and existing_direction != decision.direction

    comment = _comment(decision)
    if not cfg.enable_trading:
        prefix = f"(would first CLOSE existing {existing_direction} position) " if reversing else ""
        _log(f"ENTRY SIGNAL (DRY-RUN, V4_ENABLE_TRADING is not true -- nothing sent): "
             f"{prefix}{decision.direction.upper()} {cfg.symbol} | initial_sl={decision.initial_sl:.3f} "
             f"(far={decision.far_line}) | source={decision.source} | comment={comment!r}")
        return

    if reversing:
        # close_position() now closes EVERY open ticket for this symbol,
        # not just one -- 2026-08-31 fix, see its own docstring. Logged
        # per-ticket here (not just the raw list) so it's obvious in the
        # record how many positions actually existed and closed.
        close_results = broker.close_position(cfg, f"V4S-REVERSE-CLOSE-{int(time.time())}")
        _log(f"CLOSED {len(close_results)} existing {existing_direction} position(s) before reversing -- "
             f"results={close_results}")

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
    trap_state = TrapWatchState(cfg.trap_watch_state_file)

    mode = "LIVE (real orders will be sent)" if cfg.enable_trading else "DRY-RUN (signals printed only)"
    _log(f"connected -- watching {cfg.symbol} M1, polling every {cfg.poll_seconds}s -- {mode}")

    try:
        while True:
            run_once(cfg, state, exit_state, trap_state)
            time.sleep(cfg.poll_seconds)
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
