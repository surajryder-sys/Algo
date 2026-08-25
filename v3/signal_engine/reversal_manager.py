"""Reversal Manager -- a separate component from Trend Manager (own
magic number, own state, can hold a same- or opposite-direction
position simultaneously with Trend Manager's own trade -- confirmed
2026-08-17). Watches for price RETESTING a zone (not a fresh OB
forming, the opposite of Trend Manager's own trigger) across
H4/H2/H1/M30/M15 and M5, and reacts per the user's rules, 2026-08-18:

--- M5: immediate ---
M5's own retest fires a market order right away -- no waiting. SL =
the retested OB's own opposite edge minus/plus buffer
(entries.initial_sl, reused as-is). Stoploss Manager's existing
trailing logic takes over from there (reused, not rebuilt).

--- M5 flip while a trade is already active (XAUUSD only, 2026-08-20) ---
User-reported gap: with a reversal trade already open, _fire_m5_immediate
is never even called (run_once_symbol returns early once a trade is
active), so an M5 retest on the OPPOSITE side went completely
unevaluated -- not closed, not flipped, no reaction at all. Fixed via
_check_m5_flip: while a FILLED trade is active, an opposite-direction
M5 retest closes the current trade and opens the opposite one
immediately, same SL basis (this M5 zone's own edge) as a normal M5
immediate fire -- but ONLY if BOTH parent timeframes (M5 and M15) agree
with the new direction ("the flip is when both agrees, both disagrees
no flip"), a stricter bar than a fresh entry's own "any one" gate below,
since flipping closes a real, currently-open position. If both don't
agree, the trade stays open (same as before), but the retest is still
consumed so it isn't re-examined forever. XAUUSD-only, matching the
existing parent-gate's own scope (BTCUSD/ETHUSD have no
parent_timeframes configured, so this never runs for them).

--- M5 trap filter (XAUUSD only, fresh entry only, 2026-08-20) ---
Separate, narrower filter on TOP of the parent-alignment gate below
(_parent_aligned's own "any one of M5/M15" gate barely constrains
anything by itself here -- `direction` is almost always M5's own
just-formed newest OB too, so M5 trivially agrees with itself
regardless of what M15 says). User's own example: M15 bullish, M5's
prior OB was ALSO bullish, then M5's newest OB flips bearish and gets
retested -- that's a suspected trap/fakeout against the dominant
structure, so don't fire it immediately (mirrored for M15 bearish / M5
flips bullish). Same fallback as parent-disagreement: registers as a
waiting retest, can still fire later via M1/M3 LTF confirmation -- never
dropped outright. See _is_m5_trap's own docstring for the exact check.
Not applied to the flip above -- flipping's own both-parents-must-agree
requirement already can't fire when M15 disagrees, which is exactly
what this filter exists to catch for the weaker "any one" gate a fresh
entry uses.

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


def _parent_aligned(store: ZoneStore, symbol: str, parent_timeframes: Tuple[str, str], direction: str) -> bool:
    """True if AT LEAST ONE of the two parent timeframes' own newest OB
    currently agrees with direction -- user's rule 2026-08-19: "if
    parents agree direct fire, if any one of them agree also direct
    fire, IF BOTH disagree then wait mode." """
    return any(_parent_direction(store, symbol, tf) == direction for tf in parent_timeframes)


def _both_parents_aligned(store: ZoneStore, symbol: str, parent_timeframes: Tuple[str, str], direction: str) -> bool:
    """True only if BOTH parent timeframes' own newest OB agree with
    direction -- stricter than _parent_aligned's "any one" (used for a
    FRESH M5 entry, see above). Added 2026-08-20 for _check_m5_flip
    specifically -- user's explicit rule: flipping OUT of an already-
    active trade needs unanimous parent agreement, a higher bar than
    opening a fresh position from flat ("the flip is when both agrees,
    both disagrees no flip")."""
    return all(_parent_direction(store, symbol, tf) == direction for tf in parent_timeframes)


def _m5_recent_zones(store: ZoneStore, symbol: str, limit: int = 2) -> list:
    """The newest `limit` M5 OBs overall, bullish and bearish merged
    together and ordered purely by formation time (newest first),
    trusted (_formation_trusted) only -- as (direction, TVZone) pairs.
    Added 2026-08-20 for the M5 trap filter below, which cares about the
    raw formation SEQUENCE across both directions together (did the very
    newest M5 OB just flip against the one before it), not either
    direction's own zone list in isolation."""
    pairs = []
    for direction in ("bull", "bear"):
        pairs.extend((direction, z) for z in store.zones(symbol, "5", direction) if _formation_trusted(z))
    pairs.sort(key=lambda pair: pair[1].start_time, reverse=True)
    return pairs[:limit]


def _is_m5_trap(store: ZoneStore, symbol: str, direction: str, zone: TVZone) -> bool:
    """XAUUSD-only M5 trap filter for a FRESH immediate fire (not the
    flip -- see _check_m5_flip's own docstring for why the flip's
    stricter both-parents-agree gate already covers this case on its
    own). Added 2026-08-20, user's explicit example: "if m15 has a
    bullish ob, and m5 already has a bullish ob, and now a new bearish
    ob appears in m5 only, only this particular reversal we need to
    avoid" (mirrored for M15 bearish / M5 bullish).

    Gated on M15's own direction ONLY, not both parents -- deliberately
    narrower than _parent_aligned's own "any one of M5/M15" gate above,
    which barely constrains anything here in practice: `direction` is
    almost always M5's own current newest-formed OB too (the zone just
    got retested right after forming), so M5 trivially "agrees with
    itself" and _parent_aligned's any-one check passes regardless of
    what M15 says. This filter exists specifically to catch what that
    leaves through: M15 pointing one way, M5's own structure having just
    flipped against it on the very newest OB.

    True (skip firing) only when ALL of:
    - M15's own newest OB direction is the OPPOSITE of `direction`
      (M15 disagrees with this retest).
    - `zone` (the one just retested) IS M5's own current newest-formed
      OB (this filter only concerns the newest flip, not some older
      zone being retested later).
    - The M5 OB immediately before it (by formation, mixed bull/bear
      order -- "last but one") has the SAME direction as M15 (i.e. M5's
      own structure agreed with M15 right up until this newest one)."""
    m15_dir = _parent_direction(store, symbol, "15")
    if m15_dir is None or m15_dir == direction:
        return False  # M15 doesn't disagree with this retest -- not a trap
    recent = _m5_recent_zones(store, symbol, limit=2)
    if len(recent) < 2:
        return False
    (newest_dir, newest_zone), (second_dir, _second_zone) = recent
    if newest_zone.start_time != zone.start_time or newest_dir != direction:
        return False  # the retested zone isn't even M5's own current newest OB
    return second_dir == m15_dir


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

        # Parent-alignment gate -- XAUUSD only (sym_cfg.parent_timeframes
        # is None for BTCUSD/ETHUSD, keeping their original always-fire
        # behavior unchanged). Added 2026-08-19, user's explicit rule:
        # agreeing with at least one of Trend Manager's own two parent
        # timeframes still fires immediately below; agreeing with
        # NEITHER doesn't fire and doesn't drop the signal either -- it
        # becomes a waiting retest, resolved by the exact same M1/M3/M5
        # LTF confirmation/invalidation machinery _check_direction
        # already runs for the HTF (H4/H2/H1/M30/M15) zones. That reuse
        # also means SL for a later LTF-confirmed fire naturally comes
        # from THIS M5 zone's own edge (the multi-waiting-zone SL logic
        # in _check_direction already does that), not the confirming
        # LTF zone's -- no separate code path needed.
        if sym_cfg.parent_timeframes is not None and not _parent_aligned(store, symbol, sym_cfg.parent_timeframes, direction):
            retest_time = float(zone.retested_at) if zone.retested_at is not None else time.time()
            tracker.add_waiting(symbol, direction, WaitingRetest("5", zone.start_time, zone.top, zone.btm, retest_time))
            tracker.mark_retest_processed(symbol, "5", direction, zone.start_time)
            label = _DIRECTION_LABELS[direction]
            print(f"[reversal_manager] {symbol}: M5 {label} zone retested but neither parent agrees -- "
                  f"waiting for LTF confirmation instead of firing immediately")
            continue

        # M5 trap filter -- XAUUSD only, see _is_m5_trap's own docstring.
        # Added 2026-08-20, separate from (and narrower than) the
        # parent-alignment gate above: M15 disagreeing with this retest
        # while M5's own structure just flipped against M15 to produce
        # it is treated as a suspected fakeout -- same fallback as
        # parent-disagreement (waiting retest, can still confirm via
        # M1/M3 LTF later), not a straight fire.
        if sym_cfg.parent_timeframes is not None and _is_m5_trap(store, symbol, direction, zone):
            retest_time = float(zone.retested_at) if zone.retested_at is not None else time.time()
            tracker.add_waiting(symbol, direction, WaitingRetest("5", zone.start_time, zone.top, zone.btm, retest_time))
            tracker.mark_retest_processed(symbol, "5", direction, zone.start_time)
            label = _DIRECTION_LABELS[direction]
            print(f"[reversal_manager] {symbol}: M5 {label} zone retested but M15 disagrees and M5 just "
                  f"flipped against it -- suspected trap, waiting for LTF confirmation instead of firing immediately")
            continue

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
    immediate fire -- but ONLY if BOTH parent timeframes (M5 and M15)
    agree with the new direction ("the flip is when both agrees, both
    disagrees no flip") -- a stricter bar than a fresh M5 entry's own
    "any one" gate, since flipping means closing a real, currently-open
    position. If both don't agree, the current trade is left open (still
    no reaction -- same as before), but the retest is still consumed/
    watermarked so it isn't re-examined every cycle forever. No separate
    M5-trap check here (see _is_m5_trap's own docstring) -- requiring
    BOTH parents to agree already can't fire when M15 disagrees, which
    is exactly the trap condition that filter exists to catch for the
    weaker "any one" gate a fresh entry uses.

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
    if sym_cfg.parent_timeframes is not None:
        _close_if_opposite_ltf_ob(store, tracker, symbol)
    elif _close_if_invalidated(store, tracker, symbol):
        print(f"[reversal_manager] {symbol}: active trade's entry OB was mitigated -- treating as closed")

    _check_close_event(tracker, symbol, manual_events_file)

    active = tracker.active_trade(symbol)

    # XAUUSD-only M5 flip -- see _check_m5_flip's own docstring for the
    # real gap this closes (an opposite-direction M5 retest was
    # previously never even evaluated while a trade was active). Only
    # for a FILLED trade -- a still-PENDING order has no real position
    # to flip out of yet.
    if active is not None and active.status == "FILLED" and sym_cfg.parent_timeframes is not None:
        if _check_m5_flip(store, tracker, sym_cfg, active):
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
