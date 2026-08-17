"""Trend Manager -- the first Manager built inside Signal Engine (see
v3/signal_engine/__init__.py and the project_v3_crypto_architecture
memory note). Decides Structure and Short-term per symbol from the Data
Bridge's own OB zone data (v3/tradingview_bot/zone_store.py) -- M15 and
M5 respectively, for every symbol (this reporting pair doesn't change
per symbol, only the trade-initiation timeframes below do). No blended
"bias" or "strong/weak" label -- by explicit user decision, the two
readings are reported plainly as-is; agreement or disagreement is
visible from the two values themselves, not a separately computed
field.

Rule (user's final, confirmed 2026-08-17):
- Structure = the direction (bullish/bearish) of whichever M15 OB
  (bull or bear) formed most recently for that symbol.
- Short term = the same, but for M5.
That's the whole rule. No recency comparison BETWEEN M5 and M15, no
Strong/Weak derived label -- M15 always decides Structure, M5 always
decides Short term, full stop.

Only counts zones with formed_time_confirmed=True (see
ZoneStore.TVZone's own docstring) -- a zone whose start_time is a
wall-clock guess rather than a real Pine-confirmed formation time can't
be trusted to actually BE the most recent OB; same reasoning Alert
Manager already applies before treating a zone as real.

--- Trade initiation (added/revised 2026-08-17, user's rules verbatim) ---
See trade_tracker.py for the persisted state this enforces. There are
exactly two "parent" timeframes per symbol (see SymbolConfig.
parent_timeframes) -- each with its own permanent per-direction
watermark. The remaining "trigger" timeframes (SymbolConfig.
trigger_timeframes) are NOT independent parents and never get their own
watermark: they're pure execution triggers for whichever parent is
currently active, per the user's own words ("none of the M3, M1 will
look for the setups again without a new M5 ob" -- and the identical
framing for M15: an order can execute off M5/M3/M1, but it's the M15
zone that gets marked traded).

Parent/trigger timeframes differ per symbol -- XAUUSD: parent M5/M15,
trigger M5/M3/M1. BTCUSD/ETHUSD: parent M15/M30, trigger M15/M5 (their
own tv_scraper grid only covers H4/H2/H1/M30/M15/M5, no M1/M3 at all --
see project_tv_scraper_multi_symbol_setup memory -- so XAUUSD's scheme
simply doesn't apply and everything shifts one tier up). Set per-symbol
in config.py; everything below is written generically against whatever
SymbolConfig supplies.

Rule, in full:
1. No active trade for a symbol -> compute each direction's best PARENT
   candidate: the newer of the two parent timeframes' own newest
   eligible OB -- "whichever is recent, wins." Compare bull's best
   against bear's best the same way; the single overall winner is this
   cycle's active parent (timeframe + direction + start_time).
2. That parent only actually fires if an EXECUTION TRIGGER exists: any
   currently formed_time_confirmed OB on one of the trigger timeframes
   in the parent's direction (trivially satisfied when the parent
   itself is also a trigger timeframe). No watermark check on the
   trigger itself -- only the parent's own OB is ever persisted as
   "traded."
3. On fire: mark ONLY the parent's own (timeframe, direction) bucket as
   traded (permanent watermark advance) and open the trade. Trigger
   timeframes are never individually blocked -- next time a trade
   opens, whichever OB currently exists on them can serve as a trigger
   again.
4. Active trade open -> any NEW same-direction OB appearing on EITHER
   parent timeframe gets marked traded too, so it can't retroactively
   become a future parent (no pyramiding into an existing position).
   User's words: "similar direction ob, be it M5 or M15 [or, for
   crypto, M15 or M30], the zones gets blocked from further trades,
   because trade is already active." Opposite-direction OBs anywhere
   are left alone entirely -- that's Reversal Manager's job, not Trend
   Manager's.
5. Trade closes when its own parent OB gets mitigated (removed from the
   Data Bridge's zone store) -- current stand-in for "stopped out"
   until Execution Bridge tracks a real MT5 position.

Not yet wired to MT5 -- these are signals only ("would open a BUY"),
logged for visibility. Execution Bridge will be what actually places
an order off this later. Stoploss trailing to the nearest same-
direction OB from current price (once a trade is in favour) is
Stoploss Manager's job, not built yet -- noted here only so the intent
isn't lost.

Run with: python -m v3.signal_engine.trend_manager
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Tuple

from v3.signal_engine.config import Config, SymbolConfig, load_config
from v3.signal_engine.trade_tracker import TradeTracker
from v3.tradingview_bot.zone_store import ZoneStore

_M15 = "15"
_M5 = "5"

_DIRECTION_LABELS = {"bull": "bullish", "bear": "bearish"}
_TF_LABELS = {"240": "H4", "120": "H2", "60": "H1", "30": "M30", "15": "M15", "5": "M5", "3": "M3", "1": "M1"}


@dataclass(frozen=True)
class TrendReading:
    symbol: str
    structure: Optional[str]    # "bullish" / "bearish" / None (no M15 data yet)
    short_term: Optional[str]   # "bullish" / "bearish" / None (no M5 data yet)


def _most_recent_direction(store: ZoneStore, symbol: str, timeframe: str) -> Optional[str]:
    """Most recent OB (bull or bear, whichever is younger) for this
    symbol/timeframe, by real Pine-confirmed start_time. None if there's
    no formed_time_confirmed zone on this timeframe at all yet."""
    best_start_time: Optional[int] = None
    best_direction: Optional[str] = None
    for direction in ("bull", "bear"):
        zones = store.zones(symbol, timeframe, direction)  # newest first
        for zone in zones:
            if not zone.formed_time_confirmed:
                continue
            if best_start_time is None or zone.start_time > best_start_time:
                best_start_time = zone.start_time
                best_direction = direction
            break  # zones() is newest-first -- first confirmed one is enough per direction
    if best_direction is None:
        return None
    return _DIRECTION_LABELS[best_direction]


def compute(store: ZoneStore, symbol: str) -> TrendReading:
    return TrendReading(
        symbol=symbol,
        structure=_most_recent_direction(store, symbol, _M15),
        short_term=_most_recent_direction(store, symbol, _M5),
    )


def _format_reading(reading: TrendReading) -> str:
    structure = reading.structure or "none"
    short_term = reading.short_term or "none"
    return f"{reading.symbol}: Structure {structure}, Short term {short_term}"


def _newest_eligible_start_time(store: ZoneStore, tracker: TradeTracker, symbol: str,
                                 timeframe: str, direction: str) -> Optional[int]:
    """The newest formed_time_confirmed OB's start_time for this exact
    bucket, but only if it's still eligible (newer than the bucket's own
    permanent watermark) -- None otherwise. zones() is newest-first, and
    a watermark only ever moves forward, so the first CONFIRMED zone
    found is authoritative: if it fails eligibility, every older zone in
    this bucket does too."""
    for zone in store.zones(symbol, timeframe, direction):
        if not zone.formed_time_confirmed:
            continue
        if tracker.is_eligible(symbol, timeframe, direction, zone.start_time):
            return zone.start_time
        return None
    return None


def _has_execution_trigger(store: ZoneStore, symbol: str, direction: str,
                            trigger_timeframes: Tuple[str, ...]) -> bool:
    """Whether any of this symbol's trigger timeframes currently has ANY
    formed_time_confirmed OB in this direction -- not watermark-checked,
    since trigger timeframes are pure triggers, never individually
    persisted as traded (see module docstring point 2)."""
    for timeframe in trigger_timeframes:
        if any(z.formed_time_confirmed for z in store.zones(symbol, timeframe, direction)):
            return True
    return False


def _run_trade_logic(store: ZoneStore, tracker: TradeTracker, sym_cfg: SymbolConfig) -> None:
    """One cycle of the trade-initiation state machine for one symbol --
    see this module's own docstring for the full rule. Mutates tracker
    (persists itself); only ever prints, never touches MT5."""
    symbol = sym_cfg.symbol
    parent_timeframes = sym_cfg.parent_timeframes
    trigger_timeframes = sym_cfg.trigger_timeframes

    if tracker.close_if_invalidated(symbol, store):
        print(f"[trend_manager] {symbol}: active trade's entry OB was mitigated -- treating as closed")

    active = tracker.active_trade(symbol)

    if active is not None:
        # Already in a trade -- ANY new same-direction OB, on EITHER
        # parent timeframe, gets blocked from ever becoming a future
        # parent -- no new entry while this trade is open. Opposite-
        # direction OBs anywhere are Reversal Manager's job, not this
        # one's. (Stoploss trailing to the nearest same-direction OB is
        # Stoploss Manager's job, not built yet.)
        for timeframe in parent_timeframes:
            start_time = _newest_eligible_start_time(store, tracker, symbol, timeframe, active.direction)
            if start_time is not None:
                tracker.mark_traded_only(symbol, timeframe, active.direction, start_time)
                tf_label = _TF_LABELS.get(timeframe, timeframe)
                print(f"[trend_manager] {symbol}: new {active.direction} OB ({tf_label}) while in trade "
                      f"-- marked traded, no new entry")
        return

    # No active trade -- find the best PARENT candidate: the newer of
    # this symbol's two parent timeframes' own newest eligible OB, per
    # direction, then the newer of bull's best vs bear's best overall.
    best: Optional[Tuple[int, str, str]] = None  # (start_time, timeframe, direction)
    for timeframe in parent_timeframes:
        for direction in ("bull", "bear"):
            start_time = _newest_eligible_start_time(store, tracker, symbol, timeframe, direction)
            if start_time is not None and (best is None or start_time > best[0]):
                best = (start_time, timeframe, direction)

    if best is None:
        return
    start_time, timeframe, direction = best

    # Parent identified, but only fires if a trigger timeframe currently
    # shows an actual execution signal in this direction.
    if not _has_execution_trigger(store, symbol, direction, trigger_timeframes):
        return

    tracker.open_trade(symbol, timeframe, direction, start_time)
    label = _DIRECTION_LABELS[direction]
    tf_label = _TF_LABELS.get(timeframe, timeframe)
    trigger_label = "/".join(_TF_LABELS.get(t, t) for t in trigger_timeframes)
    print(f"[trend_manager] {symbol}: TRADE SIGNAL {label}, parent {tf_label} OB @ {start_time}, "
          f"executed via {trigger_label} trigger (not yet wired to MT5 -- signal only)")


def run_once(cfg: Config, tracker: TradeTracker) -> list[TrendReading]:
    readings = []
    for sym_cfg in cfg.symbols:
        store = ZoneStore(sym_cfg.zone_state_file)
        reading = compute(store, sym_cfg.symbol)
        readings.append(reading)
        print(f"[trend_manager] {_format_reading(reading)}")
        _run_trade_logic(store, tracker, sym_cfg)
    return readings


def main() -> None:
    cfg = load_config()
    tracker = TradeTracker(cfg.trade_state_file)
    print(f"[trend_manager] watching {[s.symbol for s in cfg.symbols]}, polling every {cfg.poll_seconds}s")
    while True:
        try:
            run_once(cfg, tracker)
        except Exception as exc:
            print(f"[trend_manager] ERROR: {exc}")
        time.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    main()
