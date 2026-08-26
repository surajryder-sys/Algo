"""Reversal Manager -- a separate component from Trend Manager (own
magic number, own state, can hold a same- or opposite-direction
position simultaneously with Trend Manager's own trade -- confirmed
2026-08-17). Watches for price RETESTING a zone (not a fresh OB
forming, the opposite of Trend Manager's own trigger) across
H4/H2/H1/M30/M15 and M5, and reacts per the user's rules, 2026-08-18:

--- M5: direct-fire only when BOTH parents agree, else waits for M1
    like every other HTF (raised from "any one parent" 2026-08-26) ---
M5's own retest checks _both_parents_aligned (M5 AND M15 must both
agree with the retest direction, same bar _check_m5_flip below already
used) -- if so, fires a PENDING limit order resting right at the zone's
own edge (entries.ob_edge), not a MARKET fill at whatever price has
drifted to by the time this poll runs. User's explicit correction,
2026-08-26: "the retest alert comes, and then market executions,
sometimes price likely moves away from zone which causes late entry."
SL = the retested OB's own opposite edge minus/plus buffer
(entries.initial_sl, reused as-is). Stoploss Manager's existing
trailing logic takes over from there once filled (reused, not rebuilt).

If both parents DON'T agree, this function does nothing at all for that
retest now -- no special M5-only fallback registration (the old "any
one agrees, else wait" gate and its M5-trap fakeout filter, both
2026-08-20, are gone as of 2026-08-26 -- see git history for
_is_m5_trap/_m5_recent_zones if ever needed again). _register_htf_m1_
retests already registers this exact same M5 zone unconditionally every
cycle regardless of parent agreement (M5 is one of XAUUSD's own
htf_m1.htf_timeframes), so an unconfirmed M5 retest waits for M1
confirmation exactly like every other HTF timeframe below, with no
separate code path needed -- the old fallback was pure duplication.

--- M5 flip while a trade is already active (XAUUSD only, 2026-08-20) ---
User-reported gap: with a reversal trade already open, _fire_m5_immediate
is never even called (run_once_symbol returns early once a trade is
active), so an M5 retest on the OPPOSITE side went completely
unevaluated -- not closed, not flipped, no reaction at all. Fixed via
_check_m5_flip: while a FILLED trade is active, an opposite-direction
M5 retest closes the current trade and opens the opposite one
immediately, same SL basis (this M5 zone's own edge) as a normal M5
direct fire -- requiring BOTH parent timeframes (M5 and M15) to agree
with the new direction ("the flip is when both agrees, both disagrees
no flip") -- same bar a fresh entry now uses too as of 2026-08-26 (they
used to differ; a fresh entry's own gate was the weaker "any one" until
that date). If both don't agree, the trade stays open (same as before),
but the retest is still consumed so it isn't re-examined forever.
XAUUSD-only, matching the existing parent-gate's own scope (BTCUSD/
ETHUSD have no parent_timeframes configured, so this never runs for
them).

--- H4/H2/H1/M30/M15: retest starts a clock, waits for LTF confirmation ---
A retest on any of these five timeframes doesn't enter immediately --
it registers a "waiting" retest (see reversal_tracker.py) and starts
watching M1/M3/M5 (M1/M3 only exist for XAUUSD -- BTCUSD/ETHUSD only
have M5, see reversal_config.py) for a FRESH same-direction OB (formed
after the retest) to confirm entry:
- M1 uses its own wider confirmation thresholds (entries.
  reversal_m1_entry: market<=4, pullback 4<d<8) -- deliberately wider
  than Trend Manager's M1, "we might catch a bottom or top... keep some
  space buffer, making sure not missing the entry" (user's words).
- M3/M5 reuse Trend Manager's own m3_entry/m5_entry exactly
  ("already prescribed entry logics").
- Whichever of M1/M3/M5 confirms first wins (closest-to-price /
  MARKET-beats-PENDING selection, same shape as Trend Manager's own).
- M1/M3/M5 zones NEVER trigger a reversal trade on their own without an
  active HTF wait behind them -- confirmation only, never independent
  entries (except M5's own special immediate-on-its-own-retest rule
  above, which is a different mechanism entirely).
- Missed (price runs past the pullback range)? Wait for the next fresh
  OB on that same LTF -- no different from Trend Manager's own
  "post-parent-formation, freshest-only" philosophy.

--- Invalidation ---
While waiting for confirmation, if M1/M3/M5 forms an OPPOSITE-direction
OB instead, the whole waiting setup is scrapped -- blocked until the
NEXT retest re-arms it (the retest that started this wait is already
permanently watermarked, so it itself can never re-trigger; only a
genuinely newer retest can start a fresh wait).

--- SL when multiple zones are waiting at once ---
"if a single candle retests multiple zones, whichever zone is at
lowerside for buy trade decides sl, similarly whichever zone is far
from the price decides the sl for sell trade" -- computed at the moment
LTF confirmation actually fires, using ALL zones currently in the
waiting list for that direction (furthest btm for buy, furthest top
for sell), not just the one that happened to trigger the wait.

One reversal trade per symbol at a time (mirrors Trend Manager's own
rule) -- not one per direction; Reversal Manager itself won't hold
simultaneous bull+bear reversal positions on the same symbol, even
though it CAN disagree with Trend Manager's own concurrent position.

Wired to Execution Bridge 2026-08-18 -- its decisions DO place real
orders once EXECUTION_BRIDGE_ENABLE_TRADING is true, same as Trend
Manager's. Reads Execution Bridge's own manual_events.py relay
(read-only) to learn about a real manual cancel/close or SL/TP hit on
a Reversal-Manager-sourced position -- without this, a stopped-out
position would leave this Manager's own state showing FILLED forever
with nothing real behind it, and Execution Bridge would keep
re-opening a brand new position for it every cycle (confirmed live).

Cold-start safeguard, added 2026-08-18 (per-bucket, see
reversal_tracker.py's own docstring for why a whole-file version wasn't
enough): the FIRST time each (symbol, timeframe, direction) bucket is
ever examined, whatever's currently already retested is seeded into the
watermark rather than fired on -- only a retest that happens AFTER that
first look is ever treated as a real signal. Confirmed live, twice: a
whole-file "first run" flag caught a genuine first-ever start (day-old
BTCUSD/ETHUSD bull retests firing immediately), but not a bucket that
simply hadn't been active before (the bear direction, on the very next
restart, fired on a real but WEEK-old retest) -- the per-bucket version
in _newest_retested_zone below is what actually closes this.

Retest recency check, added 2026-08-19 (third instance of the same
pattern): watermark ordering alone still let a real, otherwise-clean
retest fire on a zone whose own retested_at was 12 DAYS old -- the zone
likely wasn't visible in tv_scraper's own top-4 at first-look time (so
the seed above never captured it), then reappeared later carrying its
original ancient timestamp, which still looked "newer than anything
seen" to the watermark. _newest_retested_zone now also requires
retested_at to be within _RETEST_MAX_AGE_SECONDS (30 min) of wall-clock
now -- an absolute check the ordering-based watermark can never provide
by itself.

Run with: python -m v3.signal_engine.reversal_manager
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional, Tuple

from v3.execution_bridge import manual_events
from v3.signal_engine import entries, reversal_config
from v3.signal_engine.reversal_config import Config, SymbolConfig
from v3.signal_engine.reversal_tracker import ActiveReversalTrade, ReversalTracker, WaitingRetest
from v3.tradingview_bot.atr_store import AtrStore, TVAtrState
from v3.tradingview_bot.zone_store import TVZone, ZoneStore

_DIRECTION_LABELS = {"bull": "bullish", "bear": "bearish"}
_TF_LABELS = {"240": "H4", "120": "H2", "60": "H1", "30": "M30", "15": "M15", "5": "M5", "3": "M3", "1": "M1"}

# How old a retest event is allowed to be (wall-clock, retested_at vs
# now) and still count as a live "just happened" signal -- added
# 2026-08-19 after a real, otherwise-legitimate retest fired on a zone
# whose own retested_at was 12 days old (see _newest_retested_zone's
# own docstring for the full root cause). 30 minutes is generous
# against tv_scraper's own real refresh cadence (20-60s+ per timeframe,
# worse with all 3 symbols running) while still firmly rejecting
# anything hours/days stale.
_RETEST_MAX_AGE_SECONDS = 1800


def _formation_trusted(zone: TVZone) -> bool:
    """Whether this zone's own start_time can be trusted as a real
    formation time -- both currently confirmed AND never once seen
    unconfirmed. Added 2026-08-19 after a real live incident: a zone at
    Pine's ~35-day lookback ceiling read formed_time_confirmed=True on
    exactly the one poll Reversal Manager happened to check, despite
    being unconfirmed every other time -- a scrape-flakiness flicker,
    not a genuine correction, but enough to fire a real trade off a
    zone whose price range had nothing to do with current price. See
    TVZone.formed_time_ever_unconfirmed's own docstring. Replaces every
    bare `zone.formed_time_confirmed` check in this module."""
    return zone.formed_time_confirmed and not zone.formed_time_ever_unconfirmed


def _parent_direction(store: ZoneStore, symbol: str, timeframe: str) -> Optional[str]:
    """Newest trusted OB's direction (bull/bear) on this timeframe --
    v3's signal_engine-wide copy of trend_manager._most_recent_direction,
    kept local per this module's own "separate component" design (see
    module docstring). Used only for the parent-alignment gate below,
    added 2026-08-19."""
    best_start_time: Optional[int] = None
    best_direction: Optional[str] = None
    for direction in ("bull", "bear"):
        for zone in store.zones(symbol, timeframe, direction):  # newest first
            if not _formation_trusted(zone):
                continue
            if best_start_time is None or zone.start_time > best_start_time:
                best_start_time = zone.start_time
                best_direction = direction
            break
    return best_direction


def _both_parents_aligned(store: ZoneStore, symbol: str, parent_timeframes: Tuple[str, str], direction: str) -> bool:
    """True only if BOTH parent timeframes' own newest OB agree with
    direction. Added 2026-08-20 for _check_m5_flip specifically --
    user's explicit rule: flipping OUT of an already-active trade needs
    unanimous parent agreement ("the flip is when both agrees, both
    disagrees no flip"). A fresh M5 entry (_fire_m5_immediate) used a
    weaker "any one parent" gate (_parent_aligned, now removed) until
    2026-08-26, when the user raised it to this same both-parents bar;
    both call sites share this one function since."""
    return all(_parent_direction(store, symbol, tf) == direction for tf in parent_timeframes)


def _apply_sl_cap(sym_cfg: SymbolConfig, direction: str, entry_price: float, sl: float) -> float:
    """Clamps SL to sym_cfg.max_sl_points from entry if it would
    otherwise be wider -- user's rule 2026-08-19: "sl shouldn't be more
    than 20 points by default." No-op (returns sl unchanged) when
    max_sl_points is None (BTCUSD/ETHUSD, unaffected)."""
    if sym_cfg.max_sl_points is None:
        return sl
    distance = (entry_price - sl) if direction == "bull" else (sl - entry_price)
    if distance <= sym_cfg.max_sl_points:
        return sl
    return entry_price - sym_cfg.max_sl_points if direction == "bull" else entry_price + sym_cfg.max_sl_points


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


def _newest_retested_zone(store: ZoneStore, tracker: ReversalTracker, symbol: str,
                           timeframe: str, direction: str) -> Optional[TVZone]:
    """Newest zone in this bucket that's confirmed, actually retested
    (virgin=False), and a genuinely NEW retest event (start_time newer
    than the bucket's watermark) -- unlike formation-based lookups
    elsewhere, this can't short-circuit on the first confirmed zone,
    since retest status isn't monotonic with start_time.

    Distrusts retested_at == start_time (retested at the literal same
    second it formed) -- confirmed live 2026-08-18: this is the
    signature of a known tv_scraper artifact (a reused Pine array slot
    inheriting an old zone's Retested flag on first sighting, before
    it's genuinely been touched -- see scraper.py's own comment on this
    exact pattern). tv_scraper already guards one path that can produce
    this, but not every path does, so Reversal Manager distrusts it
    directly rather than assuming the upstream guard always caught it --
    same defensive spirit as formed_time_confirmed elsewhere in this
    system.

    Per-bucket cold start: the FIRST time this exact bucket is ever
    examined, seeds the watermark to whatever's currently already
    retested (if anything) instead of firing on it, then returns None
    for this cycle -- confirmed live 2026-08-18 (twice): a whole-file
    "first run" flag isn't enough, since any bucket that simply hadn't
    fired before (e.g. bear direction, when only bull had ever been
    active) looks exactly like a fresh signal otherwise, even against
    an existing state file.

    Recency check, added 2026-08-19 -- a THIRD instance of the same
    underlying pattern, confirmed live: a genuinely real, non-suspicious
    retest (real formation, real retest, correctly newer than the
    bucket's watermark, having passed both guards above) fired on a
    zone whose own retested_at was **12 days old**. Root cause: the
    zone likely wasn't visible in tv_scraper's own top-4 store at the
    time this bucket was first seeded (crowded out by newer zones), so
    it never got captured by the seed -- then reappeared later (older
    zones aging out made room again) carrying its ORIGINAL ancient
    retested_at, and to the watermark it just looked like "something
    newer than before," which is all is_new_retest actually checks.
    Watermark ordering alone can never catch this -- it needs an
    ABSOLUTE age check on the retest event itself: if retested_at is
    older than _RETEST_MAX_AGE_SECONDS, it's not trusted as a live
    "just happened" signal regardless of watermark/seeding state."""
    now = time.time()
    if not tracker.is_bucket_seeded(symbol, timeframe, direction):
        already_retested = [
            z for z in store.zones(symbol, timeframe, direction)
            if _formation_trusted(z) and not z.virgin and z.retested_at != z.start_time
        ]
        seed_start_time = max((z.start_time for z in already_retested), default=0)
        tracker.seed_bucket(symbol, timeframe, direction, seed_start_time)
        if seed_start_time:
            tf_label = _TF_LABELS.get(timeframe, timeframe)
            print(f"[reversal_manager] {symbol}: first look at {tf_label} {_DIRECTION_LABELS[direction]} -- "
                  f"skipping pre-existing retest @ {seed_start_time}, only reacting to new ones from here")
        return None

    candidates = [
        z for z in store.zones(symbol, timeframe, direction)
        if _formation_trusted(z) and not z.virgin
        and z.retested_at != z.start_time
        and (now - z.retested_at) <= _RETEST_MAX_AGE_SECONDS
        and tracker.is_new_retest(symbol, timeframe, direction, z.start_time)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda z: z.start_time)


def _newest_post_time_zone(store: ZoneStore, symbol: str, timeframe: str,
                            direction: str, after_time: int) -> Optional[TVZone]:
    """Newest confirmed OB formed strictly after after_time -- used for
    LTF confirmation/invalidation, where any fresh OB counts regardless
    of its own retest status (forming is the confirmation signal here,
    not being retested)."""
    for zone in store.zones(symbol, timeframe, direction):
        if not _formation_trusted(zone):
            continue
        if zone.start_time > after_time:
            return zone
        return None
    return None


def _fire_m5_immediate(store: ZoneStore, tracker: ReversalTracker, sym_cfg: SymbolConfig) -> bool:
    symbol = sym_cfg.symbol
    for direction in ("bull", "bear"):
        zone = _newest_retested_zone(store, tracker, symbol, "5", direction)
        if zone is None:
            continue

        if sym_cfg.parent_timeframes is not None:
            # XAUUSD only. Gate raised from _parent_aligned's "any one"
            # to _both_parents_aligned 2026-08-26 -- user's explicit
            # correction, same bar _check_m5_flip already used. Anything
            # short of both agreeing gets NO special handling here at
            # all anymore (the old any-one-else-wait fallback and its
            # M5-trap filter are gone) -- _register_htf_m1_retests
            # already registers this same M5 zone unconditionally every
            # cycle (M5 is one of XAUUSD's own htf_m1.htf_timeframes),
            # so it's already waiting for M1 confirmation "like every
            # other HTF" with no separate code path needed -- see this
            # module's own docstring for the full history.
            if not _both_parents_aligned(store, symbol, sym_cfg.parent_timeframes, direction):
                continue

            # LIMIT order resting at the zone's own edge, not a MARKET
            # fill -- user's explicit correction 2026-08-26: "the retest
            # alert comes, and then market executions, sometimes price
            # likely moves away from zone which causes late entry." No
            # pullback blending (entries.compute_entry's own distance-
            # based MARKET/PENDING split) -- always PENDING at the raw
            # edge price, same edge convention as entries.ob_edge itself
            # (bull's own first-contact edge is the zone top, bear's is
            # the zone bottom).
            edge = entries.ob_edge(direction, zone.top, zone.btm)
            sl = entries.initial_sl(symbol, "5", direction, zone.top, zone.btm)
            sl = _apply_sl_cap(sym_cfg, direction, edge, sl)
            trade = ActiveReversalTrade(direction, "5", zone.start_time, edge, sl, "PENDING",
                                         status="PENDING", opened_at=time.time(), parent_timeframe="5")
            tracker.open_trade(symbol, trade)
            tracker.mark_retest_processed(symbol, "5", direction, zone.start_time)
            label = _DIRECTION_LABELS[direction]
            print(f"[reversal_manager] {symbol}: M5 {label} zone retested, both parents agree -- "
                  f"REVERSAL TRADE PENDING limit @ {edge:.2f} (zone edge) SL={sl:.2f} "
                  f"(not yet wired to MT5 -- signal only)")
            return True

        # BTCUSD/ETHUSD -- no parent-alignment concept at all in
        # Reversal Manager (sym_cfg.parent_timeframes is None for both),
        # unchanged always-fire-MARKET behavior from before this rule
        # existed. Not in scope for the 2026-08-26 change above -- user's
        # own instruction was about the parent-agreement gate, which only
        # exists for XAUUSD to begin with.
        current_price = _read_live_close(sym_cfg.live_state_file, symbol, "5")
        if current_price is None:
            continue
        sl = entries.initial_sl(symbol, "5", direction, zone.top, zone.btm)
        sl = _apply_sl_cap(sym_cfg, direction, current_price, sl)
        trade = ActiveReversalTrade(direction, "5", zone.start_time, current_price, sl, "MARKET",
                                     status="FILLED", opened_at=time.time(), parent_timeframe="5")
        tracker.open_trade(symbol, trade)
        tracker.mark_retest_processed(symbol, "5", direction, zone.start_time)
        label = _DIRECTION_LABELS[direction]
        print(f"[reversal_manager] {symbol}: M5 {label} zone retested -- REVERSAL TRADE MARKET "
              f"@ {current_price:.2f} SL={sl:.2f} (not yet wired to MT5 -- signal only)")
        return True
    return False


def _check_m5_flip(store: ZoneStore, tracker: ReversalTracker, sym_cfg: SymbolConfig,
                    active: ActiveReversalTrade) -> bool:
    """XAUUSD-only (sym_cfg.parent_timeframes set -- same scope as
    _fire_m5_immediate's own parent-alignment gate). Added 2026-08-20
    after a real gap the user found: with a reversal trade already
    active, _fire_m5_immediate is never even called (run_once_symbol
    returns early once active is not None), so an M5 retest on the
    OPPOSITE side while a trade is open was silently never evaluated at
    all -- not closed, not flipped, nothing. User's explicit rule:
    while a trade is active, an opposite-direction M5 retest flips the
    trade -- closes the current one and opens the opposite one
    immediately, same SL basis (this M5 zone's own edge) as a normal M5
    direct fire -- ONLY if BOTH parent timeframes (M5 and M15) agree
    with the new direction ("the flip is when both agrees, both
    disagrees no flip") -- since flipping means closing a real,
    currently-open position. Fresh M5 entries used a weaker "any one"
    gate until 2026-08-26; both now share this exact same bar, so this
    flip's own gate is no longer stricter than a fresh entry's, just
    identical to it (see module docstring for the full history). If
    both don't agree, the current trade is left open (still no
    reaction -- same as before), but the retest is still consumed/
    watermarked so it isn't re-examined every cycle forever.

    Only ever called for a FILLED trade (a real, filled position -- see
    run_once_symbol) -- a still-PENDING reversal order has nothing to
    flip out of yet."""
    symbol = sym_cfg.symbol
    opposite = "bear" if active.direction == "bull" else "bull"
    zone = _newest_retested_zone(store, tracker, symbol, "5", opposite)
    if zone is None:
        return False

    if not _both_parents_aligned(store, symbol, sym_cfg.parent_timeframes, opposite):
        tracker.mark_retest_processed(symbol, "5", opposite, zone.start_time)
        label = _DIRECTION_LABELS[opposite]
        print(f"[reversal_manager] {symbol}: M5 {label} zone retested while "
              f"{_DIRECTION_LABELS[active.direction]} trade active, but both parents don't agree -- "
              f"not flipping")
        return False

    current_price = _read_live_close(sym_cfg.live_state_file, symbol, "5")
    if current_price is None:
        return False  # retry next cycle -- don't touch the active trade on missing price data

    sl = entries.initial_sl(symbol, "5", opposite, zone.top, zone.btm)
    sl = _apply_sl_cap(sym_cfg, opposite, current_price, sl)
    old_label = _DIRECTION_LABELS[active.direction]
    new_label = _DIRECTION_LABELS[opposite]

    tracker.close_trade(symbol)
    trade = ActiveReversalTrade(opposite, "5", zone.start_time, current_price, sl, "MARKET",
                                 status="FILLED", opened_at=time.time(), parent_timeframe="5")
    tracker.open_trade(symbol, trade)
    tracker.mark_retest_processed(symbol, "5", opposite, zone.start_time)
    print(f"[reversal_manager] {symbol}: M5 {new_label} zone retested with parent agreement while "
          f"{old_label} trade active -- FLIPPING to {new_label} @ {current_price:.2f} SL={sl:.2f}")
    return True


def _register_htf_retests(store: ZoneStore, tracker: ReversalTracker, sym_cfg: SymbolConfig) -> None:
    symbol = sym_cfg.symbol
    for timeframe in reversal_config.HTF_TIMEFRAMES:
        for direction in ("bull", "bear"):
            zone = _newest_retested_zone(store, tracker, symbol, timeframe, direction)
            if zone is None:
                continue
            retest_time = float(zone.retested_at) if zone.retested_at is not None else time.time()
            tracker.add_waiting(symbol, direction, WaitingRetest(timeframe, zone.start_time, zone.top, zone.btm, retest_time))
            tracker.mark_retest_processed(symbol, timeframe, direction, zone.start_time)
            tf_label = _TF_LABELS.get(timeframe, timeframe)
            label = _DIRECTION_LABELS[direction]
            print(f"[reversal_manager] {symbol}: {tf_label} {label} zone retested -- waiting for LTF confirmation")


def _prune_mitigated_waiting(store: ZoneStore, tracker: ReversalTracker, symbol: str, direction: str) -> list:
    """Drops any waiting zone that's no longer in the store (ZoneStore
    deletes on confirmed mitigation, see its own docstring) before doing
    anything else with the waiting list -- added 2026-08-20 after a real
    live incident: an M30 zone got mitigated sometime after it started a
    wait, but nothing ever re-checked it, so it sat there until an
    unrelated LTF OB happened to match direction and "confirmed" a
    setup that was already gone from the real chart. Called at the top
    of both _check_direction and _check_direction_atr_or_ob, replacing
    the bare tracker.get_waiting(...) they used to start with."""
    waiting = tracker.get_waiting(symbol, direction)
    still_valid = [w for w in waiting if store.get(symbol, w.timeframe, direction, w.start_time) is not None]
    if len(still_valid) != len(waiting):
        tracker.set_waiting(symbol, direction, still_valid)
        removed = len(waiting) - len(still_valid)
        tf_labels = ", ".join(_TF_LABELS.get(w.timeframe, w.timeframe)
                               for w in waiting if w not in still_valid)
        print(f"[reversal_manager] {symbol}: {removed} waiting {_DIRECTION_LABELS[direction]} zone(s) "
              f"({tf_labels}) mitigated while waiting -- dropped, no longer eligible to confirm")
    return still_valid


def _atr_confirms(atr_store: AtrStore, symbol: str, timeframe: str, direction: str, after_time: float) -> Optional[TVAtrState]:
    """Was v3's own copy of trend_manager._atr_confirms's identical bool
    logic; widened 2026-08-20 to check EVERY ATR period reading for this
    symbol+timeframe (OBD_ATR.pine can run more than one period on the
    same symbol+timeframe now, tagged by atr_period -- see
    AtrStore.get_all_for's own docstring), not just a single unlabeled
    one. Returns whichever period's reading both agrees with direction
    AND flipped to it strictly after after_time -- the EARLIEST such flip
    if more than one period currently qualifies, matching the user's own
    explicit reason for running two periods: "whichever gives early
    confirmation, we enter based on that." Returns None (falsy, same as
    the old bool False) if no period currently confirms; callers that
    need the confirming reading's own event_time (not just a yes/no) now
    get it directly from the returned object instead of a second lookup."""
    wants_trend = 1 if direction == "bull" else -1
    candidates = [
        s for s in atr_store.get_all_for(symbol, timeframe)
        if s.trend == wants_trend and s.event_time is not None and s.event_time > after_time
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda s: s.event_time)


# =============================================================================
# HTF-retest -> M1-only-confirm mechanism (XAUUSD only, added 2026-08-25)
# =============================================================================
# A second, fully independent reversal mechanism -- runs ALONGSIDE
# everything above (_fire_m5_immediate, _check_m5_flip, _check_direction's
# own M1/M3/M5 confirmation), never replacing it. User's own rule, verbatim
# reasoning preserved in each function's own docstring below. Shares only
# the single active-trade slot per symbol (ReversalTracker._active) --
# "one reversal trade at a time" is a hard rule regardless of which
# mechanism armed it; everything else (watermarks, seeded buckets, waiting
# list) is its own separate copy (see ReversalTracker's own docstring,
# section 4, for why: an opposite OB on M3 should only ever invalidate the
# mechanism that actually cares about M3, not silently wipe out this one's
# watch on the same underlying retest too).
#
# Flow: H4/H2/H1/M30/M15/M5 retest -> waiting state -> M1 confirms via
# EITHER a fresh M1 OB (Reversal Manager's own existing M1 entry math,
# unchanged) OR a dual ATR flip (OBD_ATR.pine's Line 1/period=2 AND Line
# 2/period=300 BOTH flipping to the same direction, strictly after the
# retest) -- the ATR path prices its own entry at a 45% pullback from the
# flip-moment price to Line 1's own trailing-stop value, as a PENDING
# order. SL comes from whichever confirmation actually fired, not the
# retest -- user's own simplification, 2026-08-25 (dropped an earlier
# retest-candle-high/low based version, one build prior, as unneeded
# complexity): the M1 OB's own opposite edge + buffer for that path, or
# Line 1's own trailing-stop value +/- buffer (direction-matched) for the
# ATR path -- see _resolve_htf_m1_confirmation's own docstring.
# Invalidated by an opposite-direction OB on M3, M5, or M15 (explicitly
# NOT M1 -- M1 is the confirmation timeframe itself) formed after the
# retest event, checked for as long as the setup is waiting OR already
# has a trade open (squares off a FILLED trade, cancels a PENDING one).

# H4/H2/H1/M30/M15/M5 -- unlike reversal_config.HTF_TIMEFRAMES (which
# excludes M5, since the original mechanism gives M5 its own dedicated
# immediate-fire treatment via _fire_m5_immediate instead), M5 is just
# another ordinary HTF retest source for THIS mechanism -- no special
# immediate-fire case, waits for M1 confirmation exactly like every other
# HTF timeframe here.
# All of this mechanism's per-symbol tuning (confirmation timeframe,
# which HTF timeframes register a wait, invalidation rules + which
# moment they anchor to, SL buffer, ATR periods, pullback fraction) now
# lives on SymbolConfig.htf_m1 (an HtfM1Config -- see reversal_config.py)
# instead of module-level constants here. Originally XAUUSD-only with
# these as bare constants; generalized 2026-08-25 once BTCUSD/ETHUSD's
# own rules were defined (different confirmation timeframe, different
# invalidation shape, different SL buffers -- module-level constants
# couldn't express per-symbol differences at all).


def _newest_retested_zone_htf_m1(store: ZoneStore, tracker: ReversalTracker, symbol: str,
                                  timeframe: str, direction: str) -> Optional[TVZone]:
    """Exact copy of _newest_retested_zone's own logic (same three guards:
    per-bucket cold start, retested_at != start_time distrust, 30-minute
    recency cap -- see that function's own docstring for the full
    reasoning behind each), just reading/writing the HTF-M1 mechanism's
    own separate tracker state instead of the original's."""
    now = time.time()
    if not tracker.is_htf_m1_bucket_seeded(symbol, timeframe, direction):
        already_retested = [
            z for z in store.zones(symbol, timeframe, direction)
            if _formation_trusted(z) and not z.virgin and z.retested_at != z.start_time
        ]
        seed_start_time = max((z.start_time for z in already_retested), default=0)
        tracker.seed_htf_m1_bucket(symbol, timeframe, direction, seed_start_time)
        if seed_start_time:
            tf_label = _TF_LABELS.get(timeframe, timeframe)
            print(f"[reversal_manager] {symbol}: (htf-m1) first look at {tf_label} {_DIRECTION_LABELS[direction]} -- "
                  f"skipping pre-existing retest @ {seed_start_time}, only reacting to new ones from here")
        return None

    candidates = [
        z for z in store.zones(symbol, timeframe, direction)
        if _formation_trusted(z) and not z.virgin
        and z.retested_at != z.start_time
        and (now - z.retested_at) <= _RETEST_MAX_AGE_SECONDS
        and tracker.is_new_htf_m1_retest(symbol, timeframe, direction, z.start_time)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda z: z.start_time)


def _register_htf_m1_retests(store: ZoneStore, tracker: ReversalTracker, sym_cfg: SymbolConfig) -> None:
    symbol = sym_cfg.symbol
    for timeframe in sym_cfg.htf_m1.htf_timeframes:
        for direction in ("bull", "bear"):
            zone = _newest_retested_zone_htf_m1(store, tracker, symbol, timeframe, direction)
            if zone is None:
                continue
            retest_time = float(zone.retested_at) if zone.retested_at is not None else time.time()
            tracker.add_htf_m1_waiting(symbol, direction,
                                        WaitingRetest(timeframe, zone.start_time, zone.top, zone.btm, retest_time))
            tracker.mark_htf_m1_retest_processed(symbol, timeframe, direction, zone.start_time)
            tf_label = _TF_LABELS.get(timeframe, timeframe)
            confirm_tf_label = _TF_LABELS.get(sym_cfg.htf_m1.confirm_timeframe, sym_cfg.htf_m1.confirm_timeframe)
            label = _DIRECTION_LABELS[direction]
            print(f"[reversal_manager] {symbol}: (htf-m1) {tf_label} {label} zone retested -- "
                  f"waiting for {confirm_tf_label} confirmation")


def _prune_mitigated_htf_m1_waiting(store: ZoneStore, tracker: ReversalTracker, symbol: str, direction: str) -> list:
    """Copy of _prune_mitigated_waiting for the HTF-M1 mechanism's own
    waiting list -- same live-incident reasoning (a mitigated zone left
    sitting in the waiting list could otherwise "confirm" a setup that's
    already gone from the real chart)."""
    waiting = tracker.get_htf_m1_waiting(symbol, direction)
    still_valid = [w for w in waiting if store.get(symbol, w.timeframe, direction, w.start_time) is not None]
    if len(still_valid) != len(waiting):
        tracker.set_htf_m1_waiting(symbol, direction, still_valid)
        removed = len(waiting) - len(still_valid)
        tf_labels = ", ".join(_TF_LABELS.get(w.timeframe, w.timeframe)
                               for w in waiting if w not in still_valid)
        print(f"[reversal_manager] {symbol}: (htf-m1) {removed} waiting {_DIRECTION_LABELS[direction]} zone(s) "
              f"({tf_labels}) mitigated while waiting -- dropped, no longer eligible to confirm")
    return still_valid


def _atr_dual_flip_confirms(atr_store: AtrStore, symbol: str, timeframe: str, direction: str, after_time: float,
                             fast_period: str, slow_period: str) -> Optional[Tuple[TVAtrState, TVAtrState]]:
    """Both OBD_ATR.pine lines (fast_period AND slow_period -- per-symbol,
    see SymbolConfig.htf_m1.atr_fast_period/atr_slow_period) must show
    direction's own trend, each having flipped to it strictly after
    after_time -- user's own rule: "we need a price flip on atr both
    atr2,300... the above event needs to occur after the retest event of
    HTF." AtrStore only ever holds a period's MOST RECENT reading, and
    OBD_ATR.pine only fires a webhook event when that period's trend
    actually flips (see pine/OBD_ATR.pine's own flipped_1/flipped_2
    gates) -- so "this period's stored event_time is after after_time"
    already means exactly "this period flipped after after_time," no
    separate history needed. Returns (fast, slow) if both currently
    confirm, else None -- callers use fast's own trail_stop for the
    pullback-price calc (see _check_htf_m1) and max(fast.event_time,
    slow.event_time) as the trade's own entry identity (the moment BOTH
    were finally aligned, not just the first one)."""
    wants_trend = 1 if direction == "bull" else -1
    fast = atr_store.get(symbol, timeframe, fast_period)
    slow = atr_store.get(symbol, timeframe, slow_period)
    if fast is None or slow is None:
        return None
    if fast.trend != wants_trend or slow.trend != wants_trend:
        return None
    if fast.event_time is None or slow.event_time is None:
        return None
    if fast.event_time <= after_time or slow.event_time <= after_time:
        return None
    return fast, slow


def _count_new_opposite_obs(store: ZoneStore, record_fn, symbol: str, direction: str, timeframe: str,
                             opposite: str, after_time: float, last_seen: Optional[int],
                             current_count: int) -> bool:
    """Shared "needs TWO distinct opposite OBs, not one" check -- used by
    both the waiting-phase and active-trade-phase halves of a
    HtfM1InvalidationRule.double_ob_timeframe rule (see that field's own
    docstring). Persisted/cross-cycle, same reasoning as trade_tracker.
    ActiveTrade.m1_opposite_ob_count: a counted OB can later get mitigated
    and vanish from the store, so this can't be re-derived from "what's
    currently live" each cycle -- record_fn (either
    ReversalTracker.record_htf_m1_waiting_double_ob or
    .record_htf_m1_active_double_ob, both take (new_sightings,
    newest_start_time)) advances the running total in whichever state
    the caller owns. zones() is newest-first, so this collects every OB
    strictly newer than the last one already counted, in case more than
    one formed within a single poll gap -- same "count don't just check"
    shape as trend_manager._close_if_m1_noise_exit's own fix. Returns
    True once the running total reaches 2."""
    since = last_seen if last_seen is not None and last_seen >= after_time else after_time
    new_sightings = [
        z for z in store.zones(symbol, timeframe, opposite)
        if _formation_trusted(z) and z.start_time > since
    ]
    if not new_sightings:
        return current_count >= 2
    newest_start_time = max(z.start_time for z in new_sightings)
    record_fn(len(new_sightings), newest_start_time)
    return (current_count + len(new_sightings)) >= 2


def _resolve_htf_m1_confirmation(store: ZoneStore, tracker: ReversalTracker, atr_store: AtrStore,
                                  sym_cfg: SymbolConfig, direction: str) -> Optional[tuple]:
    """The pure "is there a confirmed setup for this direction right now"
    check -- waiting/invalidation/candidate-selection/SL, everything
    EXCEPT actually opening a trade (that differs between a fresh fire,
    handled by _check_htf_m1, and a flip out of an already-open opposite
    trade, handled by _check_htf_m1_flip -- see that function's own
    docstring for why they need to share this instead of duplicating
    it). Returns (waiting, gate_time, mode_value, entry_price, sl,
    start_time, reason, extra_log) on a genuine confirmation, else None.

    SL comes from the CONFIRMATION itself, not the retest -- user's own
    simplification, 2026-08-25 (dropped an earlier retest-candle-high/low
    based version, one build prior, as unneeded complexity): the M1 OB
    path uses that OB's own opposite edge + buffer (entries.initial_sl,
    same "zone plus buffer" shape used everywhere else in this system);
    the ATR path uses Line 1's own trailing-stop value +/- buffer
    (matching direction -- above price for a sell, below for a buy)."""
    symbol = sym_cfg.symbol
    htf_m1 = sym_cfg.htf_m1
    confirm_tf = htf_m1.confirm_timeframe
    waiting = _prune_mitigated_htf_m1_waiting(store, tracker, symbol, direction)
    if not waiting:
        return None
    gate_time = min(w.retest_time for w in waiting)
    opposite = "bear" if direction == "bull" else "bull"

    # Invalidation -- per-symbol rule (SymbolConfig.htf_m1.
    # waiting_invalidation): any ONE opposite OB on one of
    # single_ob_timeframes, OR (if set) TWO DISTINCT opposite OBs on
    # double_ob_timeframe. XAUUSD excludes its own confirmation timeframe
    # (M1) from this entirely ("invalidation strictly if m3, m5, or m15
    # ob, but not m1 ob"); BTCUSD/ETHUSD instead give their own
    # confirmation timeframe (M3) the double-OB noise filter ("two
    # opposite ob's on m3 also invalidates"). Applies equally whether
    # this direction's own waiting setup is being evaluated for a fresh
    # fire OR as a flip candidate against an already-open opposite trade.
    rule = htf_m1.waiting_invalidation
    for timeframe in rule.single_ob_timeframes:
        zone = _newest_post_time_zone(store, symbol, timeframe, opposite, int(gate_time))
        if zone is not None:
            tracker.clear_htf_m1_waiting(symbol, direction)
            tf_label = _TF_LABELS.get(timeframe, timeframe)
            print(f"[reversal_manager] {symbol}: (htf-m1) opposite {_DIRECTION_LABELS[opposite]} OB on {tf_label} "
                  f"while waiting -- {_DIRECTION_LABELS[direction]} setup invalidated, blocked until next retest")
            return None
    if rule.double_ob_timeframe is not None:
        current_count = tracker.get_htf_m1_waiting_double_ob_count(symbol, direction)
        record_fn = lambda n, t: tracker.record_htf_m1_waiting_double_ob(symbol, direction, n, t)  # noqa: E731
        if _count_new_opposite_obs(store, record_fn, symbol, direction,
                                    rule.double_ob_timeframe, opposite, gate_time,
                                    tracker.get_htf_m1_waiting_double_ob_last_start_time(symbol, direction),
                                    current_count):
            tracker.clear_htf_m1_waiting(symbol, direction)
            tf_label = _TF_LABELS.get(rule.double_ob_timeframe, rule.double_ob_timeframe)
            print(f"[reversal_manager] {symbol}: (htf-m1) two opposite {_DIRECTION_LABELS[opposite]} OBs on "
                  f"{tf_label} while waiting -- {_DIRECTION_LABELS[direction]} setup invalidated, "
                  f"blocked until next retest")
            return None

    sl_buffer = htf_m1.sl_buffer

    current_price = _read_live_close(sym_cfg.live_state_file, symbol, confirm_tf)
    if current_price is None:
        return None

    # Confirmation A: fresh OB on the confirmation timeframe -- Reversal
    # Manager's own EXISTING confirmation entry math
    # (compute_reversal_confirm_entry, same config _check_direction's own
    # tier already uses), unchanged. User's explicit call, 2026-08-25,
    # after being asked directly (for XAUUSD's own M1): keep Reversal
    # Manager's own wider thresholds here, not Trend Manager's narrower
    # ones -- "same as per m1 ob as per trend manager" meant the same
    # SHAPE of logic (fresh-OB-confirms, market-or-pullback), not
    # literally Trend Manager's own numbers. SL is that same OB's own
    # opposite edge + buffer -- "we can add sl based on m1 itself zone
    # plus buffer" (user's own words).
    ob_candidate = None  # (mode_value, entry_price, distance, start_time, reason, sl)
    ob_zone = _newest_post_time_zone(store, symbol, confirm_tf, direction, int(gate_time))
    if ob_zone is not None:
        edge = entries.ob_edge(direction, ob_zone.top, ob_zone.btm)
        plan = entries.compute_reversal_confirm_entry(symbol, confirm_tf, direction, edge, current_price)
        if plan.mode != entries.EntryMode.NONE:
            effective_entry = current_price if plan.mode == entries.EntryMode.MARKET else plan.entry_price
            distance = 0.0 if plan.mode == entries.EntryMode.MARKET else abs(effective_entry - current_price)
            ob_sl = (ob_zone.btm - sl_buffer) if direction == "bull" else (ob_zone.top + sl_buffer)
            tf_label = _TF_LABELS.get(confirm_tf, confirm_tf)
            ob_candidate = (plan.mode.value, effective_entry, distance, ob_zone.start_time, f"{tf_label} OB", ob_sl)

    # Confirmation B: dual ATR flip (Line 1/fast AND Line 2/slow, both) --
    # PENDING order at a pullback_fraction pullback from the flip-moment
    # price to Line 1's own trailing-stop value. User's own words:
    # "record the price when atr flips, check price during the flip, see
    # the atr trailing stop value, place an order at 45% pullback from
    # detection price to trailing stop price." "Detection price" is read
    # NOW (the moment this code observes both lines have flipped) --
    # TVAtrState carries no price field of its own to look back at, same
    # "price at signal time" convention already used throughout this
    # codebase (e.g. _check_direction_atr_or_ob's own current_price
    # read). SL is that SAME trailing-stop value +/- buffer, matching
    # direction -- "atr trailing stop plus or minus buffer, for sell
    # trade and buy trade accordingly" (user's own words): above the
    # trail stop for a sell, below it for a buy.
    atr_candidate = None  # (mode_value, entry_price, distance, start_time, reason, sl, extra_log)
    flip = _atr_dual_flip_confirms(atr_store, symbol, confirm_tf, direction, gate_time,
                                    htf_m1.atr_fast_period, htf_m1.atr_slow_period)
    if flip is not None:
        fast, slow = flip
        pullback_price = current_price + htf_m1.pullback_fraction * (fast.trail_stop - current_price)
        distance = abs(pullback_price - current_price)
        start_time = int(max(fast.event_time, slow.event_time))
        atr_sl = (fast.trail_stop + sl_buffer) if direction == "bear" else (fast.trail_stop - sl_buffer)
        extra_log = f" (45% pullback from {current_price:.2f} to {fast.trail_stop:.2f})"
        atr_candidate = ("PENDING", pullback_price, distance, start_time, "ATR dual-flip", atr_sl, extra_log)

    if ob_candidate is None and atr_candidate is None:
        return None

    # Whichever gives the closer (earlier-to-fill) entry wins -- user's
    # own explicit rule, 2026-08-25: "whichever gives early entry that
    # should take... whichever has the best setup that wins." Same
    # "closest distance wins" comparison _check_direction already uses
    # when picking between its own M1/M3/M5 candidates -- a MARKET fire
    # (distance 0.0 by construction) naturally always wins over any
    # PENDING candidate, immediate beats waiting.
    candidates = [c for c in (ob_candidate, atr_candidate) if c is not None]
    mode_value, entry_price, _distance, start_time, reason, sl, *extra = min(candidates, key=lambda c: c[2])
    extra_log = extra[0] if extra else ""
    sl = _apply_sl_cap(sym_cfg, direction, entry_price, sl)
    return waiting, gate_time, mode_value, entry_price, sl, start_time, reason, extra_log


def _check_htf_m1(store: ZoneStore, tracker: ReversalTracker, atr_store: AtrStore,
                   sym_cfg: SymbolConfig, direction: str) -> bool:
    symbol = sym_cfg.symbol
    resolved = _resolve_htf_m1_confirmation(store, tracker, atr_store, sym_cfg, direction)
    if resolved is None:
        return False
    waiting, gate_time, mode_value, entry_price, sl, start_time, reason, extra_log = resolved

    status = "FILLED" if mode_value == "MARKET" else "PENDING"
    opened_at = time.time() if mode_value == "MARKET" else 0.0
    trade = ActiveReversalTrade(direction, sym_cfg.htf_m1.confirm_timeframe, start_time, entry_price, sl, mode_value,
                                 status=status, opened_at=opened_at, parent_timeframe=waiting[0].timeframe,
                                 is_htf_m1=True, htf_m1_retest_time=gate_time)
    tracker.open_trade(symbol, trade)
    tracker.clear_htf_m1_waiting(symbol, direction)
    label = _DIRECTION_LABELS[direction]
    print(f"[reversal_manager] {symbol}: (htf-m1) {reason} confirmation -- REVERSAL TRADE {mode_value} "
          f"{label} @ {entry_price:.2f}{extra_log} SL={sl:.2f} (not yet wired to MT5 -- signal only)")
    return True


def _check_htf_m1_flip(store: ZoneStore, tracker: ReversalTracker, atr_store: AtrStore,
                        sym_cfg: SymbolConfig, active: ActiveReversalTrade) -> bool:
    """While an htf-m1 trade is FILLED, keep watching the OPPOSITE
    direction's own retest/confirmation -- otherwise it's completely
    invisible (run_once_symbol only ever reaches _register_htf_m1_retests
    / _check_htf_m1 when NO trade is active at all). User's explicit
    rule, 2026-08-25: "if a reversal trade is active and if price hits
    opposite side zone... trade shouldn't be closed unless the valid buy
    setup found on opposite side... it should wait for valid
    confirmation." A mere retest of the opposite zone does nothing by
    itself (same as always -- retests only ever register a wait,
    _resolve_htf_m1_confirmation's own invalidation/confirmation checks
    are the only things that ever act on one); only once that opposite
    setup gets a GENUINE M1 confirmation (OB or ATR dual-flip, exact same
    resolution _check_htf_m1 itself uses) does anything happen: close the
    current trade, open the new opposite one, same entry/SL math either
    way. Never called for a PENDING htf-m1 trade -- same convention
    _check_m5_flip already uses ("a still-PENDING reversal order has
    nothing to flip out of yet"); the existing M5/M15 auto-close/cancel
    rule (_close_if_htf_m1_invalidated) already covers a PENDING order's
    only automatic exit."""
    symbol = sym_cfg.symbol
    opposite = "bear" if active.direction == "bull" else "bull"
    resolved = _resolve_htf_m1_confirmation(store, tracker, atr_store, sym_cfg, opposite)
    if resolved is None:
        return False
    waiting, gate_time, mode_value, entry_price, sl, start_time, reason, extra_log = resolved

    tracker.close_trade(symbol)
    status = "FILLED" if mode_value == "MARKET" else "PENDING"
    opened_at = time.time() if mode_value == "MARKET" else 0.0
    trade = ActiveReversalTrade(opposite, sym_cfg.htf_m1.confirm_timeframe, start_time, entry_price, sl, mode_value,
                                 status=status, opened_at=opened_at, parent_timeframe=waiting[0].timeframe,
                                 is_htf_m1=True, htf_m1_retest_time=gate_time)
    tracker.open_trade(symbol, trade)
    tracker.clear_htf_m1_waiting(symbol, opposite)
    old_label = _DIRECTION_LABELS[active.direction]
    new_label = _DIRECTION_LABELS[opposite]
    print(f"[reversal_manager] {symbol}: (htf-m1) opposite {reason} confirmation -- flipping {old_label} "
          f"trade to {mode_value} {new_label} @ {entry_price:.2f}{extra_log} SL={sl:.2f} "
          f"(not yet wired to MT5 -- signal only)")
    return True


def _close_if_htf_m1_invalidated(store: ZoneStore, tracker: ReversalTracker, sym_cfg: SymbolConfig,
                                  symbol: str) -> bool:
    """Only ever called for a trade with is_htf_m1=True (see
    run_once_symbol) -- squares off a FILLED trade or cancels a PENDING
    one (tracker.close_trade pops the active-trade slot either way;
    Execution Bridge's own reconcile loop then does the right real-MT5
    thing -- close a real position, or cancel a real pending order --
    based on which kind it was actually tracking).

    Per-symbol invalidation shape now comes from sym_cfg.htf_m1.active_invalidation
    (single_ob_timeframes + an optional double_ob_timeframe), and the anchor time
    from sym_cfg.htf_m1.active_invalidation_anchor:
      - XAUUSD: "retest" -> trade.htf_m1_retest_time (the original HTF retest
        event this setup was armed from, not opened_at -- user's explicit rule
        applies this "for as long as the setup is valid," which includes the
        whole waiting period BEFORE a fill too, unlike the original mechanism's
        own opposite-LTF-OB close rule (_close_if_opposite_ltf_ob), which only
        ever starts counting from the real fill moment). single_ob_timeframes
        is ("5", "15") only (NOT M3) -- user's explicit correction 2026-08-25:
        "a trade can only auto square off if a m5 or m15 opposite side ob
        forms, else it either waits for sl, or sl trail, or until opposite
        side trade gets the active setup." No double_ob_timeframe for XAUUSD.
      - BTCUSD/ETHUSD: "opened_at" -> trade.opened_at -- user's explicit
        distinction from XAUUSD: "time is important, dont refer back to older
        zones, zones should only form after entering into the trade."
        single_ob_timeframes is ("15", "30"), PLUS a double_ob_timeframe of
        "3" -- two distinct opposite M3 OBs (mirroring Trend Manager's own
        M1-exit "needs two" pattern) also invalidates, tracked via the
        trade's own htf_m1_double_ob_count/last_start_time (persists across
        polls the same way Trend Manager's M1-noise-exit counter does)."""
    trade = tracker.active_trade(symbol)
    if trade is None or not trade.is_htf_m1:
        return False
    htf_m1 = sym_cfg.htf_m1
    if htf_m1 is None:
        return False
    anchor = trade.htf_m1_retest_time if htf_m1.active_invalidation_anchor == "retest" else trade.opened_at
    if anchor is None:
        return False
    opposite = "bear" if trade.direction == "bull" else "bull"
    rule = htf_m1.active_invalidation

    for timeframe in rule.single_ob_timeframes:
        zone = _newest_post_time_zone(store, symbol, timeframe, opposite, int(anchor))
        if zone is not None:
            tracker.close_trade(symbol)
            tf_label = _TF_LABELS.get(timeframe, timeframe)
            action = "closing" if trade.status == "FILLED" else "cancelling pending"
            print(f"[reversal_manager] {symbol}: (htf-m1) opposite {_DIRECTION_LABELS[opposite]} OB formed on "
                  f"{tf_label} -- {action} {_DIRECTION_LABELS[trade.direction]} trade")
            return True

    if rule.double_ob_timeframe is not None:
        current_count = trade.htf_m1_double_ob_count

        def _record(new_sightings: int, newest_start_time: int) -> None:
            tracker.record_htf_m1_active_double_ob(symbol, new_sightings, newest_start_time)

        if _count_new_opposite_obs(store, _record, symbol, trade.direction, rule.double_ob_timeframe,
                                    opposite, float(anchor), trade.htf_m1_double_ob_last_start_time,
                                    current_count):
            tracker.close_trade(symbol)
            tf_label = _TF_LABELS.get(rule.double_ob_timeframe, rule.double_ob_timeframe)
            action = "closing" if trade.status == "FILLED" else "cancelling pending"
            print(f"[reversal_manager] {symbol}: (htf-m1) two distinct opposite "
                  f"{_DIRECTION_LABELS[opposite]} OBs formed on {tf_label} since entry -- "
                  f"{action} {_DIRECTION_LABELS[trade.direction]} trade")
            return True

    return False


def _check_direction_atr_or_ob(store: ZoneStore, tracker: ReversalTracker, sym_cfg: SymbolConfig,
                                direction: str) -> bool:
    """USOIL/USTEC's own LTF resolution -- user's explicit rule
    2026-08-19: M3 confirms (or invalidates) via EITHER a fresh OB or an
    ATR flip, whichever comes first, and a confirmed fire is always
    MARKET (no pullback/distance math -- "as its lower time frame").
    See reversal_config.SymbolConfig.atr_confirm_timeframe's own
    docstring. Structurally mirrors _check_direction above; kept as its
    own function rather than branching that one, since the confirmation
    half's shape (no distance/mode comparison, single timeframe) is
    different enough that interleaving would hurt more than it'd share."""
    symbol = sym_cfg.symbol
    timeframe = sym_cfg.atr_confirm_timeframe
    waiting = _prune_mitigated_waiting(store, tracker, symbol, direction)
    if not waiting:
        return False
    gate_time = min(w.retest_time for w in waiting)
    opposite = "bear" if direction == "bull" else "bull"
    atr_store = AtrStore(sym_cfg.atr_state_file)

    # Invalidation: a fresh opposite OB OR an opposite ATR flip on M3
    # scraps the wait -- symmetric with the confirmation side below,
    # same reasoning _check_direction already applies to its own
    # OB-only invalidation.
    opposite_zone = _newest_post_time_zone(store, symbol, timeframe, opposite, int(gate_time))
    opposite_atr = _atr_confirms(atr_store, symbol, timeframe, opposite, gate_time)
    if opposite_zone is not None or opposite_atr is not None:
        tracker.clear_waiting(symbol, direction)
        tf_label = _TF_LABELS.get(timeframe, timeframe)
        reason = "OB" if opposite_zone is not None else "ATR flip"
        print(f"[reversal_manager] {symbol}: opposite {_DIRECTION_LABELS[opposite]} {reason} on {tf_label} "
              f"while waiting -- {_DIRECTION_LABELS[direction]} setup invalidated, blocked until next retest")
        return False

    # Confirmation: fresh same-direction OB OR ATR flip, whichever first.
    zone = _newest_post_time_zone(store, symbol, timeframe, direction, int(gate_time))
    ob_confirms = zone is not None
    atr_confirms = _atr_confirms(atr_store, symbol, timeframe, direction, gate_time)
    if not ob_confirms and atr_confirms is None:
        return False

    if symbol not in entries.SYMBOL_SL_BUFFER:
        print(f"[reversal_manager] {symbol}: {_DIRECTION_LABELS[direction]} confirmation ready "
              f"({'OB' if ob_confirms else 'ATR'}) but SL buffer not configured yet -- skipping")
        return False

    current_price = _read_live_close(sym_cfg.live_state_file, symbol, timeframe)
    if current_price is None:
        return False

    sl_buffer = entries.SYMBOL_SL_BUFFER[symbol]
    if direction == "bull":
        sl_zone = min(waiting, key=lambda w: w.btm)
        sl = sl_zone.btm - sl_buffer
    else:
        sl_zone = max(waiting, key=lambda w: w.top)
        sl = sl_zone.top + sl_buffer
    sl = _apply_sl_cap(sym_cfg, direction, current_price, sl)

    start_time = zone.start_time if ob_confirms else int(atr_confirms.event_time)
    reason = "fresh OB" if ob_confirms else "ATR flip"
    trade = ActiveReversalTrade(direction, timeframe, start_time, current_price, sl, "MARKET",
                                 status="FILLED", opened_at=time.time(), exec_via_atr=not ob_confirms,
                                 parent_timeframe=sl_zone.timeframe)
    tracker.open_trade(symbol, trade)
    tracker.clear_waiting(symbol, direction)
    tf_label = _TF_LABELS.get(timeframe, timeframe)
    label = _DIRECTION_LABELS[direction]
    print(f"[reversal_manager] {symbol}: LTF confirmation via {tf_label} ({reason}) -- REVERSAL TRADE MARKET "
          f"{label} @ {current_price:.2f} SL={sl:.2f} (not yet wired to MT5 -- signal only)")
    return True


def _check_direction(store: ZoneStore, tracker: ReversalTracker, sym_cfg: SymbolConfig, direction: str) -> bool:
    symbol = sym_cfg.symbol
    waiting = _prune_mitigated_waiting(store, tracker, symbol, direction)
    if not waiting:
        return False
    gate_time = min(w.retest_time for w in waiting)
    opposite = "bear" if direction == "bull" else "bull"

    # Invalidation: a fresh opposite-direction LTF OB scraps the whole wait.
    for timeframe in sym_cfg.ltf_timeframes:
        zone = _newest_post_time_zone(store, symbol, timeframe, opposite, int(gate_time))
        if zone is not None:
            tracker.clear_waiting(symbol, direction)
            tf_label = _TF_LABELS.get(timeframe, timeframe)
            print(f"[reversal_manager] {symbol}: {_DIRECTION_LABELS[opposite]} OB on {tf_label} while waiting "
                  f"-- {_DIRECTION_LABELS[direction]} setup invalidated, blocked until next retest")
            return False

    # Confirmation: best valid entry plan across LTF timeframes.
    best = None  # (mode, entry_price, timeframe, start_time, distance, current_price)
    for timeframe in sym_cfg.ltf_timeframes:
        zone = _newest_post_time_zone(store, symbol, timeframe, direction, int(gate_time))
        if zone is None:
            continue
        current_price = _read_live_close(sym_cfg.live_state_file, symbol, timeframe)
        if current_price is None:
            continue
        edge = entries.ob_edge(direction, zone.top, zone.btm)
        plan = entries.compute_reversal_confirm_entry(symbol, timeframe, direction, edge, current_price)
        if plan.mode == entries.EntryMode.NONE:
            continue
        distance = 0.0 if plan.mode == entries.EntryMode.MARKET else abs(plan.entry_price - current_price)
        candidate = (plan.mode, plan.entry_price, timeframe, zone.start_time, distance, current_price)
        if best is None or distance < best[4]:
            best = candidate

    if best is None:
        return False

    mode, entry_price, timeframe, start_time, _distance, current_price = best
    sl_buffer = entries.SYMBOL_SL_BUFFER[symbol]
    if direction == "bull":
        sl_zone = min(waiting, key=lambda w: w.btm)
        sl = sl_zone.btm - sl_buffer
    else:
        sl_zone = max(waiting, key=lambda w: w.top)
        sl = sl_zone.top + sl_buffer

    effective_entry = current_price if mode == entries.EntryMode.MARKET else entry_price
    sl = _apply_sl_cap(sym_cfg, direction, effective_entry, sl)
    status = "FILLED" if mode == entries.EntryMode.MARKET else "PENDING"
    # PENDING leaves opened_at at its default (0.0) -- not really open
    # yet, ReversalTracker.mark_filled() sets the real value once price
    # actually crosses and it fills (see run_once_symbol).
    opened_at = time.time() if mode == entries.EntryMode.MARKET else 0.0
    trade = ActiveReversalTrade(direction, timeframe, start_time, effective_entry, sl, mode.value,
                                 status=status, opened_at=opened_at, parent_timeframe=sl_zone.timeframe)
    tracker.open_trade(symbol, trade)
    tracker.clear_waiting(symbol, direction)
    tf_label = _TF_LABELS.get(timeframe, timeframe)
    label = _DIRECTION_LABELS[direction]
    print(f"[reversal_manager] {symbol}: LTF confirmation via {tf_label} -- REVERSAL TRADE {mode.value} {label} "
          f"@ {effective_entry:.2f} SL={sl:.2f} (not yet wired to MT5 -- signal only)")
    return True


def _close_if_invalidated(store: ZoneStore, tracker: ReversalTracker, symbol: str) -> bool:
    """See ActiveReversalTrade.exec_via_atr's own docstring for why an
    ATR-confirmed trade skips this check entirely -- its entry_start_time
    is an ATR event's own timestamp, not a real OB's, so the zone lookup
    below would always come back empty and misread as "mitigated"."""
    trade = tracker.active_trade(symbol)
    if trade is None:
        return False
    if trade.exec_via_atr:
        return False
    zone = store.get(symbol, trade.entry_timeframe, trade.direction, trade.entry_start_time)
    if zone is not None:
        return False
    tracker.close_trade(symbol)
    return True


def _close_if_opposite_ltf_ob(store: ZoneStore, tracker: ReversalTracker, symbol: str) -> bool:
    """XAUUSD-only replacement for _close_if_invalidated above -- user's
    rule 2026-08-19: "lower time ob invalidation doesn't close the
    trade, but making an opposite side ob on m1 and m3 will surely
    close the trade." Only ever called for a symbol whose
    parent_timeframes is set (see run_once_symbol), so mitigation of the
    entry OB no longer closes the trade at all for that symbol -- this
    is the ONLY automatic (non-SL/TP, non-manual) close left. Checks
    M1/M3 specifically, not M5 -- deliberately narrower than the M1/M3/M5
    used for wait confirmation/invalidation elsewhere in this module.

    Gated on trade.opened_at (the trade's own REAL fill time), not
    entry_start_time (the entry OB's own formation time) -- user's
    explicit correction 2026-08-19: "it should be after the trade
    opened, time recording is very much important." Using
    entry_start_time here would let an opposite OB that already formed
    BEFORE the trade actually filled (just after the entry zone's own
    formation, but still before the real open) count as a fresh
    post-open signal."""
    trade = tracker.active_trade(symbol)
    if trade is None or trade.status != "FILLED":
        return False  # not actually open yet -- nothing to close
    opposite = "bear" if trade.direction == "bull" else "bull"
    for timeframe in ("1", "3"):
        zone = _newest_post_time_zone(store, symbol, timeframe, opposite, int(trade.opened_at))
        if zone is not None:
            tracker.close_trade(symbol)
            tf_label = _TF_LABELS.get(timeframe, timeframe)
            print(f"[reversal_manager] {symbol}: opposite {_DIRECTION_LABELS[opposite]} OB formed on {tf_label} "
                  f"-- closing {_DIRECTION_LABELS[trade.direction]} trade")
            return True
    return False


def _price_crossed(direction: str, entry_price: float, current_price: float) -> bool:
    """Same convention as trade_tracker's own -- a PENDING retracement
    entry sits below current price at proposal time for a buy (price
    must fall to reach it), above for a sell."""
    return current_price <= entry_price if direction == "bull" else current_price >= entry_price


def _check_close_event(tracker: ReversalTracker, symbol: str, manual_events_file: str) -> bool:
    """Reads Execution Bridge's own event file (manual_events.py,
    read-only from here) -- if it carries a real-world close (manual
    cancel/close, or a genuine SL/TP hit) for this symbol that Reversal
    Manager hasn't already reacted to AND that's still about whatever
    trade is currently active, closes the current trade. Returns True
    if a close happened this call. See
    ReversalTracker.should_react_to_close_event's own docstring for why
    the identity check (not just "is anything active for this symbol")
    matters -- real live Trend Manager bug, confirmed 2026-08-25, fixed
    the same way here for consistency."""
    event = manual_events.read_event(manual_events_file, symbol)
    if event is None:
        return False
    event_time, entry_timeframe, entry_start_time = event
    if not tracker.should_react_to_close_event(symbol, event_time, entry_timeframe, entry_start_time):
        return False
    if tracker.active_trade(symbol) is None:
        return False
    tracker.close_trade(symbol)
    print(f"[reversal_manager] {symbol}: real close detected in MT5 (manual/SL/TP) -- closing trade")
    return True


def run_once_symbol(store: ZoneStore, tracker: ReversalTracker, sym_cfg: SymbolConfig, manual_events_file: str) -> None:
    symbol = sym_cfg.symbol
    # XAUUSD (parent_timeframes set) uses the opposite-M1/M3-OB close
    # rule instead of mitigation-close -- see _close_if_opposite_ltf_ob's
    # own docstring. BTCUSD/ETHUSD (parent_timeframes None) keep the
    # original mitigation-close behavior, unchanged.
    # An HTF-M1 trade uses its own dedicated invalidation rule (opposite
    # OB on M3/M5/M15 after the ORIGINAL retest, not the trade's own
    # fill time) instead of either of the two below -- checked first
    # since it can fire on a still-PENDING trade too, which the other two
    # close rules don't handle at all.
    active_before = tracker.active_trade(symbol)
    if active_before is not None and active_before.is_htf_m1:
        _close_if_htf_m1_invalidated(store, tracker, sym_cfg, symbol)
    elif sym_cfg.parent_timeframes is not None:
        # XAUUSD (parent_timeframes set) uses the opposite-M1/M3-OB close
        # rule instead of mitigation-close -- see _close_if_opposite_ltf_ob's
        # own docstring. BTCUSD/ETHUSD (parent_timeframes None) keep the
        # original mitigation-close behavior, unchanged.
        _close_if_opposite_ltf_ob(store, tracker, symbol)
    elif _close_if_invalidated(store, tracker, symbol):
        print(f"[reversal_manager] {symbol}: active trade's entry OB was mitigated -- treating as closed")

    _check_close_event(tracker, symbol, manual_events_file)

    # HTF-M1's own retest registration runs unconditionally (not gated on
    # active being None like the original mechanism's own
    # _register_htf_retests below) -- otherwise the OPPOSITE direction's
    # own retest/wait would be completely invisible while a trade is
    # open, and _check_htf_m1_flip below would never have anything to
    # find. Registering the SAME direction as an already-open trade too
    # is harmless -- those entries just sit unused in the waiting list
    # until the trade closes and the mechanism resumes fresh.
    atr_store = AtrStore(sym_cfg.atr_state_file) if sym_cfg.htf_m1 is not None else None
    if sym_cfg.htf_m1 is not None:
        _register_htf_m1_retests(store, tracker, sym_cfg)

    active = tracker.active_trade(symbol)

    # XAUUSD-only M5 flip -- see _check_m5_flip's own docstring for the
    # real gap this closes (an opposite-direction M5 retest was
    # previously never even evaluated while a trade was active). Only
    # for a FILLED trade -- a still-PENDING order has no real position
    # to flip out of yet.
    if (active is not None and active.status == "FILLED" and not active.is_htf_m1
            and sym_cfg.parent_timeframes is not None):
        if _check_m5_flip(store, tracker, sym_cfg, active):
            active = tracker.active_trade(symbol)  # re-fetch -- now the new flipped trade

    # HTF-M1's own flip -- same real gap, same fix, for the new
    # mechanism's own confirmation instead of a bare M5 retest. See
    # _check_htf_m1_flip's own docstring for the user's exact rule.
    elif active is not None and active.status == "FILLED" and active.is_htf_m1:
        if _check_htf_m1_flip(store, tracker, atr_store, sym_cfg, active):
            active = tracker.active_trade(symbol)  # re-fetch -- now the new flipped trade

    if active is not None:
        if active.status == "PENDING":
            current_price = _read_live_close(sym_cfg.live_state_file, symbol, active.entry_timeframe)
            if current_price is not None and _price_crossed(active.direction, active.entry_price, current_price):
                tracker.mark_filled(symbol)
                print(f"[reversal_manager] {symbol}: REVERSAL TRADE FILLED (pending reached) "
                      f"@ {active.entry_price:.2f}")
        return  # one reversal trade at a time per symbol

    if _fire_m5_immediate(store, tracker, sym_cfg):
        return

    _register_htf_retests(store, tracker, sym_cfg)

    check_fn = _check_direction_atr_or_ob if sym_cfg.atr_confirm_timeframe is not None else _check_direction
    for direction in ("bull", "bear"):
        if check_fn(store, tracker, sym_cfg, direction):
            return

    # HTF-M1 mechanism's own fresh-fire check -- fully independent watch,
    # runs regardless of whether the original mechanism above found
    # anything this cycle. Retest registration already happened above
    # (unconditionally, before the active-trade gate) -- only the
    # confirmation check itself needs to wait until active is None.
    if sym_cfg.htf_m1 is not None:
        for direction in ("bull", "bear"):
            if _check_htf_m1(store, tracker, atr_store, sym_cfg, direction):
                return


def run_once(cfg: Config, tracker: ReversalTracker) -> None:
    for sym_cfg in cfg.symbols:
        store = ZoneStore(sym_cfg.zone_state_file)
        try:
            run_once_symbol(store, tracker, sym_cfg, cfg.manual_events_file)
        except Exception as exc:
            print(f"[reversal_manager] {sym_cfg.symbol} ERROR: {exc}")


def main() -> None:
    cfg = reversal_config.load_config()
    tracker = ReversalTracker(cfg.state_file)
    print(f"[reversal_manager] watching {[s.symbol for s in cfg.symbols]}, polling every {cfg.poll_seconds}s")
    while True:
        try:
            run_once(cfg, tracker)
        except Exception as exc:
            print(f"[reversal_manager] ERROR: {exc}")
        time.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    main()
