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
Only counts zones with formed_time_confirmed=True (see
ZoneStore.TVZone's own docstring) -- a zone whose start_time is a
wall-clock guess rather than a real Pine-confirmed formation time can't
be trusted to actually BE the most recent OB; same reasoning Alert
Manager already applies before treating a zone as real.

=== Trade initiation + entry execution (2026-08-17, user's rules) ===
See trade_tracker.py for the persisted state this enforces, and
entries.py for the entry-price/SL math (v3's own copy of algo_v2's
shape, not an import -- see that module's docstring).

--- Bias (parent OB) ---
Two "parent" timeframes per symbol (SymbolConfig.parent_timeframes),
each with its own permanent per-direction watermark. Whichever parent
has the newer eligible OB wins bias + direction ("whichever is recent,
wins"). XAUUSD: M5/M15. BTCUSD/ETHUSD: M15/M30 (their own tv_scraper
grid has no M1/M3 at all -- see project_tv_scraper_multi_symbol_setup
-- so XAUUSD's scheme doesn't apply and everything shifts one tier up).

While a trade is open (pending or filled), any NEW same-direction OB on
EITHER parent timeframe gets marked traded too (no pyramiding). An
OPPOSITE-direction, eligible parent OB appearing on EITHER parent
timeframe FLIPS the bias -- closes and permanently blocks the current
trade (same treatment as a manual cancel), and the opposite side
becomes the new active bias. This is deliberately simple/mechanical
(not the future Reversal Manager's job -- that's a separate, more
sophisticated component the user is deferring; this is just "opposite
parent OB shows up -> get out," confirmed in-scope for Trend Manager
itself 2026-08-17).

--- Entry execution, once bias is set ---
Trigger timeframes (SymbolConfig.trigger_timeframes) are pure execution
triggers -- XAUUSD: M5/M3/M1. BTCUSD/ETHUSD: M15/M5. Each only reacts
to an OB on its own timeframe that formed AFTER the parent OB's own
formation time -- not any OB that already exists (applies to all
trigger timeframes, confirmed explicitly for M3, implied for M5).

Distance is measured fresh each time a NEW post-parent trigger OB is
first seen, using that timeframe's own CURRENT close price (from
tv_scraper's live snapshot -- deliberately TradingView-sourced, not
MT5: "through tv scraper is best, mt5 only for placing orders and
getting live price," keeping Trend Manager MT5-free). M1 uses new
thresholds (market<=3, pullback 3<d<6, floor 3). M3/M5 reuse v2's old
thresholds unchanged (market<=4, pullback 4<d<12, floor 4). Once
computed, a PENDING entry's price is fixed -- not continuously
re-computed every cycle; it either fills (price reaches it) or gets
cancelled-and-replaced by a NEWER, BETTER candidate.

Best-setup selection is continuous: every cycle, all trigger timeframes
with a valid (non-NONE) plan off a genuinely newer post-parent OB are
compared -- MARKET (fills instantly, distance 0) always beats PENDING;
among the same mode, closer to current price wins (mirrors algo_v2's
own choose_winning_candidate). If the winner differs from whatever's
currently proposed, cancel-and-replace: NOT blocked, that superseded OB
stays eligible for later (mirrors algo_v2's own bot-cancel vs
manual-cancel split in intervention.py's expected_cancellations).

SL = the PARENT OB's own opposite edge (bull: its bottom; bear: its
top), buffered by that symbol's own sl_buffer -- NOT whichever trigger
timeframe (M1/M3/M5) actually executed the entry. Changed 2026-08-19,
user's explicit correction ("whoever opens the trade, they should
follow parent ob sl") -- the trigger OB only ever decided entry price/
timing, never SL, going forward. Previously (2026-08-18) this used the
executed trigger OB's own opposite edge instead; before that, an even
earlier "closest edge" cross-timeframe search (see
entries.initial_sl_from_parent vs the still-present entries.initial_sl,
which Reversal Manager's own M5-immediate case continues to use).

--- Blocking (permanent, never releases on mitigation) ---
An OB only gets permanently blocked (its own bucket's watermark
advanced) when: (a) its order actually FILLS (market fills instantly;
pending fills when price crosses the fixed entry price), (b) it's
flip-closed by an opposite parent OB, or (c) the user manually cancels
a pending order or manually closes an open position in MT5. Getting
cancelled-and-replaced by a BETTER system-chosen setup does NOT block
it -- that's the real fix over algo_v2's existing behavior, where only
the one timeframe that got used blocks, letting a different timeframe
immediately grab the same underlying opportunity right after a manual
cancel.

(c) is detected by v3/execution_bridge/'s own intervention.py (real MT5
history, not simulated) and relayed here read-only via
manual_events.py's small event file -- see
TradeTracker.should_react_to_close_event and this module's own
_check_close_event. Trend Manager never touches MT5 itself; it only
reads that one small file Execution Bridge writes.

Stop-vs-market fallback: if price has already moved into range by the
time a pending order would be placed, fire market instead of a stop
that would just trigger instantly anyway -- this IS what compute_entry
already does (distance <= market_max -> MARKET), no separate code path
needed.

Signal-only here -- this module only ever decides and logs.
v3/execution_bridge/ is what actually places/cancels/closes real MT5
orders off these decisions (own EXECUTION_BRIDGE_ENABLE_TRADING flag,
defaults false like every other bot in this repo).

Run with: python -m v3.signal_engine.trend_manager
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from v3.execution_bridge import manual_events
from v3.signal_engine import entries
from v3.signal_engine.config import Config, SymbolConfig, load_config
from v3.signal_engine.trade_tracker import ActiveTrade, TradeTracker
from v3.tradingview_bot.zone_store import TVZone, ZoneStore

_M15 = "15"
_M5 = "5"

_DIRECTION_LABELS = {"bull": "bullish", "bear": "bearish"}
_TF_LABELS = {"240": "H4", "120": "H2", "60": "H1", "30": "M30", "15": "M15", "5": "M5", "3": "M3", "1": "M1"}


@dataclass(frozen=True)
class TrendReading:
    symbol: str
    structure: Optional[str]    # "bullish" / "bearish" / None (no M15 data yet)
    short_term: Optional[str]   # "bullish" / "bearish" / None (no M5 data yet)


def _formation_trusted(zone: TVZone) -> bool:
    """Whether this zone's own start_time can be trusted as a real
    formation time -- both currently confirmed AND never once seen
    unconfirmed. Added 2026-08-19, v3's signal_engine-wide copy of
    Reversal Manager's own helper of the same name (see that module's
    docstring for the live incident: formed_time_confirmed can flicker
    True on a single poll for a genuinely old zone due to ordinary
    scrape flakiness, which is enough to slip past a bare
    `zone.formed_time_confirmed` check). Replaces every such bare check
    in this module too, for the same reason -- Trend Manager's bias and
    trigger-OB selection are exactly as exposed to this as Reversal
    Manager's retest selection was."""
    return zone.formed_time_confirmed and not zone.formed_time_ever_unconfirmed


def _most_recent_direction(store: ZoneStore, symbol: str, timeframe: str) -> Optional[str]:
    """Most recent OB (bull or bear, whichever is younger) for this
    symbol/timeframe, by real Pine-confirmed start_time. None if there's
    no formed_time_confirmed zone on this timeframe at all yet."""
    best_start_time: Optional[int] = None
    best_direction: Optional[str] = None
    for direction in ("bull", "bear"):
        zones = store.zones(symbol, timeframe, direction)  # newest first
        for zone in zones:
            if not _formation_trusted(zone):
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


# ---------------------------------------------------------------------------
# Trade initiation + entry execution
# ---------------------------------------------------------------------------

def _newest_eligible_start_time(store: ZoneStore, tracker: TradeTracker, symbol: str,
                                 timeframe: str, direction: str) -> Optional[int]:
    """The newest formed_time_confirmed OB's start_time for this exact
    PARENT-timeframe bucket, but only if it's still eligible (newer than
    the bucket's own permanent watermark) -- None otherwise. zones() is
    newest-first, and a watermark only ever moves forward, so the first
    CONFIRMED zone found is authoritative: if it fails eligibility,
    every older zone in this bucket does too.

    Cold-start seeded, added 2026-08-19 after a real live incident: an
    M15 bearish OB over an hour old, never previously watermarked in
    that bucket, got treated as "the newest eligible parent" purely
    because start_time > 0 (the default watermark) -- flipping bias off
    a genuinely stale zone. v3's own copy of reversal_manager.py's
    identical fix: the FIRST time this exact bucket is ever examined,
    whatever's currently there gets seeded into the watermark instead of
    being treated as a fresh signal -- only a zone that appears AFTER
    that first look can ever set or flip bias from this bucket."""
    if not tracker.is_bucket_seeded(symbol, timeframe, direction):
        trusted = [z for z in store.zones(symbol, timeframe, direction) if _formation_trusted(z)]
        seed_start_time = max((z.start_time for z in trusted), default=0)
        tracker.seed_bucket(symbol, timeframe, direction, seed_start_time)
        if seed_start_time:
            tf_label = _TF_LABELS.get(timeframe, timeframe)
            print(f"[trend_manager] {symbol}: first look at {tf_label} {_DIRECTION_LABELS[direction]} parent -- "
                  f"skipping pre-existing OB @ {seed_start_time}, only reacting to new ones from here")
        return None

    for zone in store.zones(symbol, timeframe, direction):
        if not _formation_trusted(zone):
            continue
        if tracker.is_eligible(symbol, timeframe, direction, zone.start_time):
            return zone.start_time
        return None
    return None


def _best_parent_candidate(store: ZoneStore, tracker: TradeTracker, symbol: str,
                            parent_timeframes: Tuple[str, str],
                            directions: Tuple[str, ...]) -> Optional[Tuple[int, str, str]]:
    """(start_time, timeframe, direction) of the single newest eligible
    OB across the given parent timeframes and directions, or None."""
    best: Optional[Tuple[int, str, str]] = None
    for timeframe in parent_timeframes:
        for direction in directions:
            start_time = _newest_eligible_start_time(store, tracker, symbol, timeframe, direction)
            if start_time is not None and (best is None or start_time > best[0]):
                best = (start_time, timeframe, direction)
    return best


def _newest_post_parent_zone(store: ZoneStore, tracker: TradeTracker, symbol: str, timeframe: str,
                              direction: str, parent_start_time: int) -> Optional[TVZone]:
    """The newest formed_time_confirmed, still-eligible OB on this
    trigger bucket that formed AFTER the parent OB itself -- None if no
    such OB exists. Same newest-first short-circuit logic as
    _newest_eligible_start_time."""
    for zone in store.zones(symbol, timeframe, direction):
        if not _formation_trusted(zone):
            continue
        if zone.start_time <= parent_start_time:
            return None  # newest confirmed zone isn't even post-parent -> nothing older is either
        if not tracker.is_eligible(symbol, timeframe, direction, zone.start_time):
            return None  # newest confirmed+post-parent zone already blocked -> nothing newer/eligible exists
        return zone
    return None


def _read_live_close(path: str, symbol: str, timeframe: str) -> Optional[float]:
    p = Path(path)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    entry = raw.get(f"{symbol}|{timeframe}")
    if entry is None:
        return None
    close = entry.get("close")
    return float(close) if close is not None else None


def _price_crossed(direction: str, entry_price: float, current_price: float) -> bool:
    """True once price has come back to (or through) a pending
    retracement entry -- bull entries sit below current price at
    proposal time (price must fall to reach them), bear entries sit
    above (price must rise)."""
    return current_price <= entry_price if direction == "bull" else current_price >= entry_price


def _try_fire_entry(store: ZoneStore, tracker: TradeTracker, sym_cfg: SymbolConfig, active: ActiveTrade) -> None:
    """Scans trigger timeframes for the best current entry-plan
    candidate and, if it's new/better than whatever's already proposed,
    fires it (MARKET fills immediately, PENDING gets proposed/replaced).
    Mutates tracker; only ever prints."""
    symbol = sym_cfg.symbol
    best_plan = None  # (mode, entry_price, timeframe, start_time, distance, current_price, top, btm)
    for timeframe in sym_cfg.trigger_timeframes:
        zone = _newest_post_parent_zone(store, tracker, symbol, timeframe, active.direction, active.parent_start_time)
        if zone is None:
            continue
        current_price = _read_live_close(sym_cfg.live_state_file, symbol, timeframe)
        if current_price is None:
            continue
        edge = entries.ob_edge(active.direction, zone.top, zone.btm)
        plan = entries.compute_entry(symbol, timeframe, active.direction, edge, current_price)
        if plan.mode == entries.EntryMode.NONE:
            continue
        distance = 0.0 if plan.mode == entries.EntryMode.MARKET else abs(plan.entry_price - current_price)
        candidate = (plan.mode, plan.entry_price, timeframe, zone.start_time, distance, current_price, zone.top, zone.btm)
        if best_plan is None or distance < best_plan[4]:
            best_plan = candidate

    if best_plan is None:
        return

    mode, entry_price, timeframe, start_time, _distance, current_price, top, btm = best_plan
    already_proposed = (active.status != "AWAITING_TRIGGER"
                         and active.exec_timeframe == timeframe and active.exec_start_time == start_time)
    if already_proposed:
        return

    # SL is based on the PARENT OB's own edge, not whichever trigger
    # timeframe (M1/M3/M5) actually fired the entry -- user's explicit
    # correction 2026-08-19 (see entries.initial_sl_from_parent's own
    # docstring). The parent zone is re-fetched fresh here rather than
    # cached on ActiveTrade since its top/btm never change once formed,
    # but re-fetching costs nothing and avoids a second source of truth.
    # A parent zone this trade's own bias depends on being missing from
    # the store would mean something upstream is already badly wrong
    # (bias-flip/mitigation logic should have reacted first) -- skip
    # firing rather than fall back to a DIFFERENT SL basis than what was
    # decided, silently.
    parent_zone = store.get(symbol, active.parent_timeframe, active.direction, active.parent_start_time)
    if parent_zone is None:
        print(f"[trend_manager] {symbol}: parent OB ({active.parent_timeframe}, "
              f"{active.parent_start_time}) missing from the store -- skipping this cycle's entry")
        return
    sl = entries.initial_sl_from_parent(symbol, active.direction, parent_zone.top, parent_zone.btm)
    tf_label = _TF_LABELS.get(timeframe, timeframe)
    direction_label = _DIRECTION_LABELS[active.direction]

    if mode == entries.EntryMode.MARKET:
        tracker.fill_market(symbol, timeframe, start_time, current_price, sl)
        print(f"[trend_manager] {symbol}: TRADE SIGNAL {direction_label} FILLED (market) via {tf_label} "
              f"@ {current_price:.2f} SL={sl} (not yet wired to MT5 -- signal only)")
    else:
        was_pending = active.status == "PENDING"
        tracker.propose_pending(symbol, timeframe, start_time, entry_price, sl)
        verb = "replaced with" if was_pending else "proposed"
        print(f"[trend_manager] {symbol}: TRADE SIGNAL {direction_label} PENDING {verb} via {tf_label} "
              f"@ {entry_price:.2f} SL={sl} (not yet wired to MT5 -- signal only)")


def _check_close_event(tracker: TradeTracker, symbol: str, manual_events_file: str) -> bool:
    """Reads Execution Bridge's own event file (manual_events.py,
    read-only from here) -- if it carries a real-world close timestamp
    (manual cancel/close, or a genuine SL/TP hit) for this symbol that
    Trend Manager hasn't already reacted to, closes and permanently
    blocks the current trade, same treatment as a bias flip. Returns
    True if a close happened this call."""
    event_time = manual_events.read_event_time(manual_events_file, symbol)
    if event_time is None or not tracker.should_react_to_close_event(symbol, event_time):
        return False
    if tracker.active_trade(symbol) is None:
        return False  # nothing currently open/pending to close
    tracker.close_trade(symbol, block=True)
    print(f"[trend_manager] {symbol}: real close detected in MT5 (manual/SL/TP) -- closing trade, blocking that OB")
    return True


def _run_trade_logic(store: ZoneStore, tracker: TradeTracker, sym_cfg: SymbolConfig,
                      manual_events_file: str) -> None:
    """One cycle of the full trade-initiation + entry-execution state
    machine for one symbol -- see this module's own docstring for the
    full rule. Mutates tracker (persists itself); only ever prints,
    never touches MT5."""
    symbol = sym_cfg.symbol
    parent_timeframes = sym_cfg.parent_timeframes

    if tracker.close_if_invalidated(symbol, store):
        print(f"[trend_manager] {symbol}: active trade's reference OB was mitigated -- treating as closed")

    _check_close_event(tracker, symbol, manual_events_file)

    active = tracker.active_trade(symbol)

    # Opposite-direction parent OB flips the bias -- close/block current, if any.
    if active is not None:
        opposite_direction = "bear" if active.direction == "bull" else "bull"
        flip = _best_parent_candidate(store, tracker, symbol, parent_timeframes, (opposite_direction,))
        if flip is not None:
            print(f"[trend_manager] {symbol}: opposite-direction parent OB appeared -- bias flip, "
                  f"closing {active.direction} trade")
            tracker.close_trade(symbol, block=True)
            active = None

    # No active trade (never was, or just flipped) -- decide bias.
    if active is None:
        best = _best_parent_candidate(store, tracker, symbol, parent_timeframes, ("bull", "bear"))
        if best is None:
            return
        start_time, timeframe, direction = best
        tracker.set_parent(symbol, direction, timeframe, start_time)
        active = tracker.active_trade(symbol)
        tf_label = _TF_LABELS.get(timeframe, timeframe)
        print(f"[trend_manager] {symbol}: bias {_DIRECTION_LABELS[direction]} via parent {tf_label} "
              f"OB @ {start_time}")

    # Block any NEW same-direction parent OB on EITHER parent timeframe (no pyramiding).
    for timeframe in parent_timeframes:
        start_time = _newest_eligible_start_time(store, tracker, symbol, timeframe, active.direction)
        if start_time is not None:
            tracker.mark_traded_only(symbol, timeframe, active.direction, start_time)
            tf_label = _TF_LABELS.get(timeframe, timeframe)
            print(f"[trend_manager] {symbol}: new {active.direction} OB ({tf_label}) while trade active "
                  f"-- marked traded, no new entry")

    if active.status == "FILLED":
        return  # nothing more to do until closure (mitigation) or a bias flip

    _try_fire_entry(store, tracker, sym_cfg, active)

    active = tracker.active_trade(symbol)  # re-fetch -- _try_fire_entry may have mutated it
    if active is not None and active.status == "PENDING":
        current_price = _read_live_close(sym_cfg.live_state_file, symbol, active.exec_timeframe)
        if current_price is not None and _price_crossed(active.direction, active.entry_price, current_price):
            tracker.fill_pending(symbol)
            tf_label = _TF_LABELS.get(active.exec_timeframe, active.exec_timeframe)
            print(f"[trend_manager] {symbol}: TRADE SIGNAL {_DIRECTION_LABELS[active.direction]} FILLED "
                  f"(pending reached) via {tf_label} @ {active.entry_price:.2f} "
                  f"(not yet wired to MT5 -- signal only)")


def run_once(cfg: Config, tracker: TradeTracker) -> list[TrendReading]:
    readings = []
    for sym_cfg in cfg.symbols:
        store = ZoneStore(sym_cfg.zone_state_file)
        reading = compute(store, sym_cfg.symbol)
        readings.append(reading)
        print(f"[trend_manager] {_format_reading(reading)}")
        _run_trade_logic(store, tracker, sym_cfg, cfg.manual_events_file)
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
