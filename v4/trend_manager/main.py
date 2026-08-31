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
    day -- "no need of m3 and m5 now" -- this is M1-only end to end for
    ENTRY-TIMEFRAME purposes; the same MT5-native bridge is also read a
    second time at M5 purely for its zones, see below).
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
    natively cover M5/M3/M1.
  - MT5-native M5 zones, ALSO folded into the same edge-gap filter,
    labeled "M5(MT5)" to distinguish from the TV-scraper "M5" above --
    added 2026-08-31, user's explicit request: "sometimes zones in
    tradingview dont form but mt5 forms soon". Same
    OB_Zone_Bridge_Lite.mq5 bridge/schema as M1's own read, just called a
    second time with tf_minutes=5 (mql5/OB_Zone_Bridge_Lite.mq5 was
    already attached to an M5 XAUUSD chart from V4's original bridge
    build -- OBSTATE_LITE_XAUUSD_5.json already existed, just unread
    until now). Two independent M5 zone sources now feed the same
    filter; a rejection log line's edge label tells you which one
    actually blocked the entry.
  - MT5-native M2 and M4 ATR (dual-trail structure only, not zones) --
    added 2026-08-31 alongside two new XAUUSD charts (M2, then M4 once
    the user filled in every chart from M1 to M5) the user attached the
    same indicators to. READ/STORE ONLY per explicit instruction ("no
    decision impact yet") -- logged every poll (see run_once's own
    "(store-only)" lines) but never passed into evaluate_entry,
    tv_structure, or the edge-gap buffer list above. Revisit if/when
    either is given an actual role.

Safety: V4_ENABLE_TRADING must be explicitly set to true in .env for any
order to actually be sent -- left unset (default false), every resolved
decision is printed but nothing touches the account. Matches every other
bot in this repo (see CLAUDE.md).
"""
from __future__ import annotations

import datetime
import time
from dataclasses import dataclass

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


@dataclass
class LabeledZone:
    """Wraps a raw zone (either v4.bridge.reader.Zone -- M1's own -- or
    v4.bridge.tv_zones.Zone -- a buffer timeframe) with which timeframe it
    came from, since neither underlying Zone class carries that itself.
    Added 2026-08-31, user's explicit request ("give me the reason as
    well, which timeframe edge") -- the combined M1+buffer edge-gap check
    previously reported only the winning edge's price, with no way to
    tell whether it came from M1's own zones or one of the H4-M5 buffer
    timeframes. Extended 2026-08-31, user's explicit request ("have all
    the values") -- carries virgin/start_time too now, not just
    high/low/label, so a rejection log line can record the FULL zone
    (see m1_execution.py's _EdgedZone/evaluate_entry) since the live
    bridge's own rolling zone history can move on and lose the exact
    zone before anyone asks about it after the fact."""
    high: float
    low: float
    label: str
    virgin: bool
    start_time: int


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


def _buffer_zones(symbol: str, structure: str) -> list[LabeledZone]:
    """All opposing-direction zones across the six buffer timeframes --
    bear zones (no_long) when the flip is bullish (STRONG), bull zones
    (no_short) when it's bearish (WEAK). Returns [] (fails open, not
    closed) if the scraper's zone file is missing/mid-write this poll --
    same transient-failure tolerance as every other bridge read here.
    Each zone is tagged with its own timeframe label (see LabeledZone)."""
    zones = read_all_zones(symbol)
    if zones is None:
        return []
    out: list[LabeledZone] = []
    for label in _BUFFER_TIMEFRAMES:
        tf = zones.get(label)
        if tf is None:
            continue
        raw = tf.bear if structure == "STRONG" else tf.bull
        out.extend(LabeledZone(high=z.high, low=z.low, label=label, virgin=z.virgin, start_time=z.start_time) for z in raw)
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

    # M2/M4 ATR -- added 2026-08-31, user's explicit request: read/store
    # only for now, no decision impact ("M2's ATR structure ... no
    # decision impact yet") -- M4 added the same day once the user
    # attached the same indicators to a 5th chart ("added m1 to m5, 5
    # charts on mt5"), same store-only treatment, no instruction given to
    # do otherwise. Logged every poll purely so it's in the record; not
    # passed into evaluate_entry and not part of tv_structure/mt5_atr
    # above. Revisit if/when either gets an actual role in the entry
    # logic.
    for label, minutes in (("M2", 2), ("M4", 4)):
        snap = read_atr_dual(cfg.symbol, minutes)
        if snap is not None and not snap.is_stale():
            _log(f"{label} (store-only) structure={snap.structure} "
                 f"line1={snap.line1.trend:+d}@{snap.line1.trail_stop:.3f} "
                 f"line2={snap.line2.trend:+d}@{snap.line2.trail_stop:.3f}")
        else:
            _log(f"{label} MT5 ATR bridge missing/stale -- nothing to store this poll")

    # MT5-only for M1 execution -- TradingView's own flip is deliberately
    # not read here anymore (see this module's own docstring).
    tv_structure = None

    ob = read_zone_lite(cfg.symbol, 1)
    opposing_zones: list[LabeledZone] = []
    if ob is not None and not ob.is_stale():
        raw = ob.bear if mt5_atr.structure == "STRONG" else ob.bull
        opposing_zones = [LabeledZone(high=z.high, low=z.low, label="M1", virgin=z.virgin, start_time=z.start_time) for z in raw]
    else:
        _log("M1 OB bridge missing/stale -- proceeding with no M1 edge-gap zones known")

    # MT5-native M5 zones -- added 2026-08-31, user's explicit request:
    # "sometimes zones in tradingview dont form but mt5 forms soon". The
    # TV-scraper's own "M5" buffer (see _buffer_zones/_BUFFER_TIMEFRAMES
    # below) reads TradingView's OBD_SecretTrader.pine, which can lag or
    # simply not print a zone the MT5-native OB_Zone_Bridge_Lite indicator
    # already sees on the same bar. Labeled "M5(MT5)" (not "M5") so a
    # rejection log line always shows which of the two independent M5
    # sources actually blocked the entry. Same bridge/schema as M1's own
    # read above (OBSTATE_LITE_XAUUSD_5.json) -- just a different
    # tf_minutes argument, no new MQL5 work needed.
    mt5_m5_zones: list[LabeledZone] = []
    ob_m5 = read_zone_lite(cfg.symbol, 5)
    if ob_m5 is not None and not ob_m5.is_stale():
        raw_m5 = ob_m5.bear if mt5_atr.structure == "STRONG" else ob_m5.bull
        mt5_m5_zones = [LabeledZone(high=z.high, low=z.low, label="M5(MT5)", virgin=z.virgin, start_time=z.start_time) for z in raw_m5]
    else:
        _log("MT5-native M5 OB bridge missing/stale -- proceeding with no M5(MT5) edge-gap zones known")

    previous_close = broker.find_previous_candle_close(cfg.symbol, mt5_atr.structure_event_time)
    if previous_close is None:
        _log(f"could not find the flip candle (structure_event_time={mt5_atr.structure_event_time}) "
             f"in MT5 history -- skipping this poll")
        return

    current_price = broker.get_mid_price(cfg.symbol)
    buffer_zones = _buffer_zones(cfg.symbol, mt5_atr.structure) + mt5_m5_zones

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
        close_result = broker.close_position(cfg, f"V4S-REVERSE-CLOSE-{int(time.time())}")
        _log(f"CLOSED existing {existing_direction} position before reversing -- result={close_result}")

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
