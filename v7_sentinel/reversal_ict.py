"""RM-ICT -- Reversal Manager's SECOND component. FULL REDESIGN
(confirmed with the user 2026-09-18, "full replacement") -- this is NOT
the M15-Primary-Structure-gated ST1F/M3F/ST3F/M5F system this module
used to run (see git history / v5_sentinel/reversal_ict.py for that
design). No more M15 Primary Structure or bar-close flip-tracker dependency at
all -- this component is now purely
OB-zone-touch + CISD-confirmation driven.

ENTRY RULE:

  A zone is a CANDIDATE once it's TOUCHED (zone.retested -- live price
  has genuinely re-entered its range, nlb_nsb_watcher.py's own
  independently-computed flag, same "not about the flip, it's about the
  retest" philosophy this module always used) and hasn't been traded (or
  substantially overlapped one already traded, see
  ICTEligibilityStore.overlaps_traded()).

  TOUCH MUST BE LIVE AND RECENT (user, 2026-09-21, 30 minutes): the first
  live session fired a BUY on an H4 zone that had been "retested" ~4 days
  earlier (retested_source "seed" -- copied from the scraper's history, all
  21 retested zones in the block were like that). So a touch only counts if
  the watcher saw it itself (retested_source "live") AND it was at most
  cfg.ict_touch_max_age_minutes ago (_touch_is_current()); after that the
  zone is simply no longer a candidate. A zone the block already knew was
  tested before we ever watched it can never be one (these are untested-zone
  reversals -- a first touch only).

  Once touched, wait for a FRESH CISD confirmation in the MATCHING
  direction (bullish zone needs bullish CISD, bearish needs bearish),
  sourced from a timeframe pool that depends on the ZONE's OWN
  timeframe (_ZONE_CISD_POOLS below):
    - H4/H2/H1/M30/M15/M10 zones -> M3 or M5 CISD, whichever fires first.
    - M5 zones (own exception) -> M1, M3, or M5's own CISD, whichever
      fires first.
  Every pool is checked every cycle -- "whichever gives faster
  confirmation" falls out naturally from checking all of them each poll
  and acting the instant any one fires, no explicit race/timer needed.

  M3 ZONES REMOVED (2026-09-22, user: "remove m3 zones for reversals,
  lets keep it from all HTF to M5") -- RM-ICT's own candidate zones now
  span H4 down to M5 only (see ob_levels.TIMEFRAMES); M3 stays fully in
  use as a CONFIRMATION timeframe (both pools above), only M3-FORMED OB
  zones are no longer candidates.

  M1 REPLACED BY M10 (2026-09-22, same scraper grid change, see
  ob_levels.py's own docstring) -- M10 is a brand-new zone timeframe
  here, given the same (3, 5) confirmation pool as every other HTF zone
  (H4/H2/H1/M30/M15); M1 was never a zone timeframe in this module to
  begin with (it only ever showed up as a CONFIRMATION pool entry for M5
  zones above), so nothing about the M5 exception pool changes.

  SEQUENCING -- the critical rule, user's own words: "if m3 is already
  in bullish, we dont do anything, we wait an event to occur post the
  touch, not before the touch... everything will be here in sequence
  wise, recency matters." A CISD that's ALREADY sitting in the matching
  direction AT touch-time does NOT count -- it needs a genuinely FRESH
  confirmation that happens AFTER the touch. This is why the check below
  uses cisd_bridge.fresh_cisd() (the "privileged, momentary, only
  non-None the EXACT bar it confirmed" contract already used elsewhere
  in this project for one-shot triggers), not cisd_bridge.read_cisd()
  (the standing/current state -- a deliberately DIFFERENT contract for
  a different purpose). A standing-but-stale CISD from before the touch would
  already have been "fresh" on ITS OWN bar, long past -- by the time a
  zone becomes eligible (touched) and this check starts running against
  it every cycle, only a confirmation that fires on some LATER cycle can
  ever be caught here, with no explicit touch-timestamp-vs-CISD-bar-time
  comparison needed (which would have been awkward anyway:
  zone.retested_at is wall-clock, cisd.last_cisd_time is bar-time --
  different clock domains).

SL: by default the zone's own edge plus a buffer. Bullish zone (BUY) ->
zone.btm ("ob low") minus buffer. Bearish zone (SELL) -> zone.top
("ob high") plus buffer.

SL-DISTANCE OVERRIDE (confirmed with the user 2026-09-19): if the
zone-edge SL sits farther than cfg.ict_sl_override_points (15 for XAUUSD)
from the live entry price (ask for a BUY, bid for a SELL, measured after
buffer), look for a BETTER SL -- the sole trigger is SL distance (zone
size is not a condition). The candidate is picked by the same rule
RM-STR uses (sl_basis.py): the FARTHEST usable ATR-dual / Supertrend line
on M5, else on M3, else CISD's own swing high/low, plus buffer. The whole
point is to REDUCE risk, so that candidate is then checked against the
zone-edge SL: if its final SL is not strictly TIGHTER, it is REJECTED and
the zone-edge SL is kept unchanged -- user's own words: "if the new sl is
22 points, dont take new sl of 22 points, then apply same sl". This is a
plain reject, not a re-search: it does not look for a nearer line, and
it does not fall through to M3/swing after M5's pick was rejected. The
trade still fires either way -- the signal is never skipped over this. A
replacement is only required to be tighter, not necessarily under 15
points itself.

ELIGIBILITY ("traded" tracking): unchanged from before -- kept in THIS
module's OWN separate state file (ICTEligibilityStore below), never
written back into the shared NLB/NSB Block itself (owned exclusively by
nlb_nsb_watcher.py). A zone fires AT MOST ONCE in its lifetime here.
overlaps_traded() remains the geometric backstop against the same real
zone re-firing under a churned identity (see that class's own
docstring for the confirmed V5S incident this guards against).

Position lifecycle: identical to STR's own (the caller's own
_process_signal, generalized to accept either component) -- no position
-> open fresh. Opposite direction -> square off + reopen. SAME
direction -> NO-OP, just mark this zone traded. Credited under its own
"V7S-RM-ICT-{tag}" comment prefix.

MT5-SOURCED ZONES + CROSS-SOURCE DEDUP (2026-09-24, user: "get them from
MT5 bridge M15, M5, M3 keep them in data manager classify TV and MT5
zones we need reversal trades to be fired based on Mt5 zones as well...
if a ict trade is already fired in a direction and if TV qualifies, skip
it, also vice versa"): find_ict_signals() now scans TWO zone sources --
the existing TV-only NLB/NSB Block (H4-M10, unchanged) AND the new merged
TV+MT5 store (ict_ob_block.ICTBlockStore, M15/M5/M3, Data Manager's own
ict_ob_watcher.py). _ZONE_CISD_POOLS is UNCHANGED -- it has no "3" entry
(M3 zones are an EXIT-only concept, confirmed with the user; RM's own
entry pool stays H4/H2/H1/M30/M15/M10/M5 exactly as before), so any
M3-timeframe zone from the merged store is scanned but can never actually
produce a signal (_check_zone's own pool lookup returns None for it) --
no special-casing needed. M15/M5 TV-sourced zones now exist in BOTH
stores at once (the old Block already covered them); this is harmless,
not a double-entry risk -- see below.

CROSS-SOURCE DEDUP NEEDS NO NEW MECHANISM: both stores' zones feed the
SAME eligibility/position-lifecycle machinery below, scoped to RM-ICT's
one shared magic number (cfg.ict_magic_number) regardless of which
store/source a zone came from. _process_signal's own existing "a
same-direction position is already open -> NO-OP, mark traded, alert
only" branch (see that function's own docstring) already means: once
EITHER source fires a real BUY, any LATER-qualifying BUY signal from the
OTHER source that same or a later cycle finds that position already open
and just no-ops -- exactly "if a trade is already fired in a direction
and the other source qualifies, skip it" with zero new state to build or
maintain."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from v7_sentinel import cisd_bridge, flip_state, sl_basis
from v7_sentinel.ict_ob_block import ICTBlockStore
from v7_sentinel.nlb_nsb_block import BlockStore

# Which CISD timeframe(s) can confirm a touched zone of a given
# timeframe, in the order checked each cycle -- see module docstring.
# Keyed by BlockZone.timeframe (ob_levels.py's own raw scraper key).
_ZONE_CISD_POOLS: dict[str, tuple[int, ...]] = {
    "240": (3, 5),   # H4
    "120": (3, 5),   # H2
    "60": (3, 5),    # H1
    "30": (3, 5),    # M30
    "15": (3, 5),    # M15
    "10": (3, 5),    # M10 -- new 2026-09-22, replaces M1 in the scraper grid
    "5": (1, 3, 5),  # M5 -- own exception, also accepts M1/M3
    # no "3" entry -- M3 zones removed 2026-09-22, see module docstring
}

_CISD_TAG = {3: "M3CD", 5: "M5CD"}   # no "1" entry any more -- M1 is a structure FLIP now, not a CISD, see _check_zone()


@dataclass(frozen=True)
class ICTSignal:
    direction: int              # 1 buy, -1 sell
    zone_id: str
    timeframe_name: str          # "H1"
    zone_top: float
    zone_btm: float
    role: str                     # "no_short_buffer" (demand/bull) | "no_long_buffer" (supply/bear) -- the zone's own role, threaded through to mark_traded() so overlaps_traded() can stay role-aware (see ICTEligibilityStore's own docstring, 2026-09-24 fix)
    trigger: str                   # "M1FLIP" | "M3CD" | "M5CD" | "M1FLIP-DIRECT" -- whichever confirmation fired first (M1 dual-ATR structure FLIP replaced M1 CISD 2026-09-24, see _check_zone()'s own docstring)
    sl: float
    sl_source: str                  # "ZONE" (plain zone edge) | "M5/ATR2" | "M3/ST" | "SWING" (only when it replaced a too-far zone SL with a tighter one)
    confirm_bar_time: int              # the triggering event's own bar_time -- see trend_main.py's ENTRY_BRIDGE_LAG_ALERT_SECONDS. For "M1FLIP-DIRECT" this is the CURRENT M1 bar's own time, not a fresh confirmation -- reversal_main.py's lag check skips both direct-fire and flip triggers (neither is CISD-bridge-specific).


class ICTEligibilityStore:
    """Persists which OB zones (by their own stable Block zone_id) this
    component has already traded -- see module docstring for why this
    is a SEPARATE file from the Block's own, not written back into it.

    ALSO persists each traded zone's own (top, btm, role) -- a real V5S
    incident: the exact same real price level fired an RM-ICT trade
    TWICE in one day, ~16 hours apart -- not a dedup failure by zone_id,
    the two firings genuinely had different zone_ids, because
    tv_scraper's own detection had churned the real zone out of its own
    visible list and back in between the two, minting it a brand-new
    start_time/identity the second time -- same debounce-churn mechanism
    as the fabricated-zone incidents, just reproducing a REAL level's
    identity instead of inventing one. is_traded() alone can't catch
    this -- it only ever compares exact zone_ids. overlaps_traded()
    below is the geometric backstop: reject a new zone outright if its
    own range substantially overlaps a zone already traded today,
    regardless of whether the two share an identity. Zones persisted
    before this field existed carry None for their own range (no
    historical top/btm was ever recorded for them) -- they simply never
    gain overlap protection retroactively, which is fine; this store
    isn't pruned at all, so old entries stay forever either way.

    ROLE-AWARE (fixed 2026-09-24, real live incident: an already-traded
    BULLISH zone was silently blocking THREE separate BEARISH zones --
    M30/H1/H2, all touched within minutes of each other -- purely
    because their price ranges overlapped; overlaps_traded() never
    checked role at all, so it treated "the same level rediscovered"
    (its actual purpose) the same as "a completely different, opposite-
    direction zone that happens to sit at a similar price" (a real,
    common occurrence in any ranging market). Now the traded range also
    carries the zone's own role, and only a match of the SAME role
    counts as an overlap -- a bullish/demand zone can never block a
    bearish/supply one again, or vice versa. Entries saved before this
    fix (a bare (top, btm) pair, no role) are loaded with role=None and
    can never match going forward -- same "no retroactive protection"
    policy this store's own docstring already established for the
    top/btm-less entries above."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._traded: dict[str, Optional[tuple]] = {}   # zone_id -> (top, btm, role) | None
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
            if isinstance(raw, list):
                # Pre-existing older format: a bare list of zone_id
                # strings, no range ever recorded.
                self._traded = {zid: None for zid in raw}
            else:
                loaded: dict[str, Optional[tuple]] = {}
                for zid, v in raw.items():
                    if v is None:
                        loaded[zid] = None
                    elif len(v) >= 3:
                        loaded[zid] = (v[0], v[1], v[2])
                    else:
                        # Pre-role format (top, btm) -- role unknown, never matches the
                        # role-aware overlap check below (see class docstring's own ROLE-AWARE section).
                        loaded[zid] = (v[0], v[1], None)
                self._traded = loaded
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self._traded = {}

    def _save(self) -> None:
        payload = {zid: (list(rng) if rng is not None else None) for zid, rng in self._traded.items()}
        self._path.write_text(json.dumps(payload))

    def is_traded(self, zone_id: str) -> bool:
        return zone_id in self._traded

    def mark_traded(self, zone_id: str, top: Optional[float] = None, btm: Optional[float] = None,
                    role: Optional[str] = None) -> None:
        if zone_id not in self._traded:
            self._traded[zone_id] = (top, btm, role) if top is not None and btm is not None else None
            self._save()

    def overlaps_traded(self, top: float, btm: float, role: str, min_overlap_fraction: float = 0.6) -> Optional[str]:
        """Returns the zone_id of an already-traded zone of the SAME role
        whose own range overlaps [btm, top] by at least
        min_overlap_fraction of the SMALLER of the two ranges, or None if
        nothing overlaps that much. Fractional (not exact-match)
        deliberately -- a re-detected copy of the same real level isn't
        guaranteed to read pixel-identical top/btm on rediscovery, just
        substantially the same range. Role-checked (2026-09-24, see class
        docstring) -- a different or unknown-role entry never matches,
        no matter how much its range overlaps."""
        for zone_id, rng in self._traded.items():
            if rng is None:
                continue
            traded_top, traded_btm, traded_role = rng
            if traded_role != role:
                continue
            overlap = min(top, traded_top) - max(btm, traded_btm)
            if overlap <= 0:
                continue
            smaller_range = min(top - btm, traded_top - traded_btm)
            if smaller_range <= 0:
                continue
            if overlap / smaller_range >= min_overlap_fraction:
                return zone_id
        return None


def _check_zone(zone, symbol: str, m1_fs: Optional["flip_state.FlipStateResult"] = None,
                m5_structure: Optional[int] = None, m15_structure: Optional[int] = None):
    """(direction, trigger_tag, cisd_for_sl, direct_fire, bar_time) if
    this TOUCHED, otherwise-eligible zone has a qualifying confirmation
    THIS cycle from its own timeframe's pool -- None if not (no
    confirmation yet, or an unrecognized zone timeframe, e.g. M3, which
    _ZONE_CISD_POOLS deliberately has no entry for -- see module
    docstring). direct_fire (see M1 DIRECT FIRE below) tells the caller
    to skip the SL-distance override entirely and use the plain zone
    edge, always False for every other path.

    M1 DUAL-ATR FLIP REPLACES M1 CISD (2026-09-24, user: "there also use
    m1 dual atr flip, instead of m1 cisd, rest m3 cisd is good"): the M1
    slot in M5 zones' own exception pool (_ZONE_CISD_POOLS["5"]) is now a
    genuine M1 ATR-dual structure FLIP (flip_state.fresh_flip_direction(),
    the same "privileged, momentary" one-shot contract as fresh_cisd() --
    confirmed with the user the same day for exit_manager_ltf.py's
    identical substitution, applied here for consistency), not a CISD
    confirmation. M3/M5 stay exactly CISD-based, unchanged.

    cisd_for_sl: for the M3/M5 CISD-triggered paths this is the real
    triggering CISDState, exactly as before. For the M1FLIP paths there
    is no CISD event at all -- this instead carries M3's own CURRENT
    standing CISD (cisd_bridge.read_cisd(symbol, 3), which may be None if
    that bridge happens to be stale) purely as a last-resort swing-basis
    provider for sl_basis.initial_sl_basis()'s own fallback; that
    function's own None-guard (cisd_bridge.sl_basis()) means a
    genuinely-unavailable swing just yields "no valid override", never a
    crash.

    M1 GATE (2026-09-22, user's own words: "to get into a trade based on
    m1 cisd, lets say sell trade, m5 or m15 should be bearish or weak" --
    unchanged in spirit for the flip): a fresh M1 flip additionally
    requires ITS OWN direction to match EITHER the M5 or M15 ATR-dual
    confirmed structure (m5_structure/m15_structure: 1 strong/bullish, -1
    weak/bearish, None unknown). If neither agrees, M1 is skipped for
    this touch (NOT a hard fail -- the pool keeps checking M3/M5
    normally). M3/M5 confirmations are never gated by this.

    M1 DIRECT FIRE (2026-09-22, M5 zones only, user's own words: "sometime
    the cisd is already bullish to fire on m1 cisd on bullish ob... in
    such cases we can use direct fire on zone edge" -- now keyed off M1's
    CURRENT confirmed structure rather than a standing CISD
    classification): the fresh check above only ever fires on a NEW flip
    event AFTER the touch -- a structure that was ALREADY confirmed in
    the matching direction at touch time (or ever since) never re-flips
    and so could never fire M1 for this zone at all under that rule
    alone, potentially missing a genuinely good entry indefinitely. This
    is a SEPARATE, ADDITIONAL path, checked only when the fresh check
    found nothing: if M1's CURRENT confirmed structure (m1_fs.confirmed)
    already matches this zone's own direction, AND the same M5/M15
    structure agreement the fresh-M1 gate requires also holds, fire
    immediately using the zone's own edge as SL, skipping the normal
    SL-distance override search entirely (tagged "M1FLIP-DIRECT" to stay
    distinguishable from a genuine fresh-event "M1FLIP" fire in logs/
    journal). M5 zones only -- M1 is never offered to any other zone
    timeframe's pool to begin with."""
    direction = 1 if zone.role == "no_short_buffer" else -1  # bullish OB -> BUY, bearish OB -> SELL
    pool = _ZONE_CISD_POOLS.get(zone.timeframe)
    if pool is None:
        return None

    m1_flip_direction = flip_state.fresh_flip_direction(m1_fs) if m1_fs is not None else None

    for tf_minutes in pool:
        if tf_minutes == 1:
            if m1_flip_direction != direction:
                continue
            if m5_structure != direction and m15_structure != direction:
                continue   # M1 needs M5 or M15 structure agreement -- keep checking the rest of the pool
            return direction, "M1FLIP", cisd_bridge.read_cisd(symbol, 3), False, m1_fs.last_event.bar_time
        cisd = cisd_bridge.fresh_cisd(symbol, tf_minutes)
        if cisd is None or cisd_bridge.direction_of(cisd) != direction:
            continue
        return direction, _CISD_TAG[tf_minutes], cisd, False, cisd.bar_time

    if zone.timeframe == "5" and m1_fs is not None:
        if (m1_fs.confirmed.value == direction
                and (m5_structure == direction or m15_structure == direction)):
            return direction, "M1FLIP-DIRECT", cisd_bridge.read_cisd(symbol, 3), True, m1_fs.last_time

    return None


def _touch_is_current(zone, max_age_minutes: float, now: float) -> bool:
    """True only for a touch this component may act on: the watcher saw price
    enter the zone LIVE (retested_source "live" -- a "seed" retest was copied
    from the scraper's history, we never observed it) and it happened within
    the last max_age_minutes. retested_at is wall-clock for live touches, so
    it compares directly with time.time()."""
    if not zone.retested or zone.retested_source != "live" or zone.retested_at is None:
        return False
    return 0 <= now - zone.retested_at <= max_age_minutes * 60


def find_ict_signals(symbol: str, block_state_file: str, ict_block_state_file: str,
                     eligibility: ICTEligibilityStore,
                     sl_buffer: float, bid: float, ask: float, sl_override_points: float,
                     touch_max_age_minutes: float, now: Optional[float] = None,
                     m1_fs: Optional["flip_state.FlipStateResult"] = None,
                     m5_structure: Optional[int] = None, m15_structure: Optional[int] = None) -> list[ICTSignal]:
    """Every currently-valid (not invalidated -- an invalidated zone is
    already deleted from its own Block outright), TOUCHED, untraded OB
    zone with a qualifying confirmation this cycle -- see module
    docstring for the full design, including the SL-distance override
    and the MT5-SOURCED ZONES + CROSS-SOURCE DEDUP section. Scans TWO
    stores every call, read-only: the TV-only NLB/NSB Block
    (block_state_file, H4-M10) and the merged TV+MT5 store
    (ict_block_state_file, M15/M5/M3). bid/ask are the live prices, used
    only as the entry price for the SL-distance test. m1_fs/m5_structure/
    m15_structure: see _check_zone()'s own docstring for the M1 FLIP
    gate/direct-fire this feeds (m1_fs: reversal_main.py's own shared
    BridgeBarFlipTracker, updated for tf_minutes=1)."""
    store = BlockStore(block_state_file)
    ict_store = ICTBlockStore(ict_block_state_file)
    signals: list[ICTSignal] = []
    cache: dict = {}
    now = time.time() if now is None else now

    for zone in store.zones() + ict_store.zones():
        if not _touch_is_current(zone, touch_max_age_minutes, now):
            continue
        if eligibility.is_traded(zone.zone_id):
            continue
        dup = eligibility.overlaps_traded(zone.top, zone.btm, zone.role)
        if dup is not None:
            print(f"[V7S-ICT] zone {zone.zone_id} [{zone.btm:.3f}-{zone.top:.3f}] skipped -- "
                  f"substantially overlaps already-traded zone {dup}")
            continue

        result = _check_zone(zone, symbol, m1_fs, m5_structure, m15_structure)
        if result is None:
            continue
        direction, trigger, cisd, direct_fire, bar_time = result

        sl = zone.btm - sl_buffer if direction == 1 else zone.top + sl_buffer
        sl_source = "ZONE"
        entry_price = ask if direction == 1 else bid
        if not direct_fire and abs(entry_price - sl) > sl_override_points:
            # Only a strictly TIGHTER SL replaces it (must_beat_sl) -- None means
            # nothing would reduce risk, so the zone-edge SL stays as-is. Skipped
            # entirely for a direct-fire signal (M1FLIP-DIRECT) -- always the plain
            # zone edge, see _check_zone()'s own M1 DIRECT FIRE docstring. cisd here
            # is the real triggering CISD for M3CD/M5CD, or M3's own best-effort
            # standing state (possibly None) for M1FLIP -- see _check_zone()'s own
            # cisd_for_sl docstring.
            resolved = sl_basis.initial_sl_basis(symbol, direction, entry_price, cisd, cache,
                                                 sl_buffer=sl_buffer, must_beat_sl=sl)
            if resolved is not None:
                basis, sl_source = resolved
                sl = basis - sl_buffer if direction == 1 else basis + sl_buffer

        signals.append(ICTSignal(
            direction=direction, zone_id=zone.zone_id, timeframe_name=zone.timeframe_name,
            zone_top=zone.top, zone_btm=zone.btm, role=zone.role, trigger=trigger, sl=sl, sl_source=sl_source,
            confirm_bar_time=bar_time,
        ))

    return signals
