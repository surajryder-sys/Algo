"""RM-ICT -- Reversal Manager's SECOND component. FULL REDESIGN
(confirmed with the user 2026-09-18, "full replacement") -- this is NOT
the M15-Primary-Structure-gated ST1F/M3F/ST3F/M5F system this module
used to run (see git history / v5_sentinel/reversal_ict.py for that
design). No more M15 Primary Structure, `structure.py`, or
BridgeBarFlipTracker dependency at all -- this component is now purely
OB-zone-touch + CISD-confirmation driven.

ENTRY RULE:

  A zone is a CANDIDATE once it's TOUCHED (zone.retested -- live price
  has genuinely re-entered its range, nlb_nsb_watcher.py's own
  independently-computed flag, same "not about the flip, it's about the
  retest" philosophy this module always used) and hasn't been traded (or
  substantially overlapped one already traded, see
  ICTEligibilityStore.overlaps_traded()).

  Once touched, wait for a FRESH CISD confirmation in the MATCHING
  direction (bullish zone needs bullish CISD, bearish needs bearish),
  sourced from a timeframe pool that depends on the ZONE's OWN
  timeframe (_ZONE_CISD_POOLS below):
    - H4/H2/H1/M30/M15 zones -> M3 or M5 CISD, whichever fires first.
    - M3 zones (own exception) -> M1 or M3's own CISD, whichever fires
      first.
    - M5 zones (own exception) -> M1, M3, or M5's own CISD, whichever
      fires first.
  Every pool is checked every cycle -- "whichever gives faster
  confirmation" falls out naturally from checking all of them each poll
  and acting the instant any one fires, no explicit race/timer needed.

  SEQUENCING -- the critical rule, user's own words: "if m3 is already
  in bullish, we dont do anything, we wait an event to occur post the
  touch, not before the touch... everything will be here in sequence
  wise, recency matters." A CISD that's ALREADY sitting in the matching
  direction AT touch-time does NOT count -- it needs a genuinely FRESH
  confirmation that happens AFTER the touch. This is why the check below
  uses cisd_bridge.fresh_cisd() (the "privileged, momentary, only
  non-None the EXACT bar it confirmed" contract already used elsewhere
  in this project for one-shot triggers), not cisd_bridge.read_cisd()
  (the standing/current state, which structure.py's own arbitration
  uses instead, deliberately a DIFFERENT contract for a different
  purpose). A standing-but-stale CISD from before the touch would
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
"V6S-RM-ICT-{tag}" comment prefix.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from v6_sentinel import cisd_bridge, sl_basis
from v6_sentinel.nlb_nsb_block import BlockStore, BlockZone

# Which CISD timeframe(s) can confirm a touched zone of a given
# timeframe, in the order checked each cycle -- see module docstring.
# Keyed by BlockZone.timeframe (ob_levels.py's own raw scraper key).
_ZONE_CISD_POOLS: dict[str, tuple[int, ...]] = {
    "240": (3, 5),   # H4
    "120": (3, 5),   # H2
    "60": (3, 5),    # H1
    "30": (3, 5),    # M30
    "15": (3, 5),    # M15
    "3": (1, 3),     # M3 -- own exception, also accepts M1
    "5": (1, 3, 5),  # M5 -- own exception, also accepts M1/M3
}

_CISD_TAG = {1: "M1CD", 3: "M3CD", 5: "M5CD"}


@dataclass(frozen=True)
class ICTSignal:
    direction: int              # 1 buy, -1 sell
    zone_id: str
    timeframe_name: str          # "H1"
    zone_top: float
    zone_btm: float
    trigger: str                  # "M1CD" | "M3CD" | "M5CD" -- whichever CISD timeframe fired first
    sl: float
    sl_source: str                  # "ZONE" (plain zone edge) | "M5/ATR2" | "M3/ST" | "SWING" (only when it replaced a too-far zone SL with a tighter one)


class ICTEligibilityStore:
    """Persists which OB zones (by their own stable Block zone_id) this
    component has already traded -- see module docstring for why this
    is a SEPARATE file from the Block's own, not written back into it.

    ALSO persists each traded zone's own (top, btm) range -- a real V5S
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
    isn't pruned at all, so old entries stay forever either way."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._traded: dict[str, Optional[tuple]] = {}
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
                self._traded = {zid: (tuple(v) if v is not None else None) for zid, v in raw.items()}
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self._traded = {}

    def _save(self) -> None:
        payload = {zid: (list(rng) if rng is not None else None) for zid, rng in self._traded.items()}
        self._path.write_text(json.dumps(payload))

    def is_traded(self, zone_id: str) -> bool:
        return zone_id in self._traded

    def mark_traded(self, zone_id: str, top: Optional[float] = None, btm: Optional[float] = None) -> None:
        if zone_id not in self._traded:
            self._traded[zone_id] = (top, btm) if top is not None and btm is not None else None
            self._save()

    def overlaps_traded(self, top: float, btm: float, min_overlap_fraction: float = 0.6) -> Optional[str]:
        """Returns the zone_id of an already-traded zone whose own range
        overlaps [btm, top] by at least min_overlap_fraction of the
        SMALLER of the two ranges, or None if nothing overlaps that
        much. Fractional (not exact-match) deliberately -- a re-detected
        copy of the same real level isn't guaranteed to read pixel-
        identical top/btm on rediscovery, just substantially the same
        range."""
        for zone_id, rng in self._traded.items():
            if rng is None:
                continue
            traded_top, traded_btm = rng
            overlap = min(top, traded_top) - max(btm, traded_btm)
            if overlap <= 0:
                continue
            smaller_range = min(top - btm, traded_top - traded_btm)
            if smaller_range <= 0:
                continue
            if overlap / smaller_range >= min_overlap_fraction:
                return zone_id
        return None


def _check_zone(zone: BlockZone, symbol: str):
    """(direction, trigger_tag, cisd) if this TOUCHED, otherwise-eligible
    zone has a fresh, matching-direction CISD confirmation THIS cycle
    from its own timeframe's pool -- None if not (wrong/no fresh CISD
    yet, or an unrecognized zone timeframe, e.g. M1, which
    _ZONE_CISD_POOLS deliberately has no entry for -- see module
    docstring). The confirming CISD object is returned too, since the
    SL-distance override may need its swing as the last-resort basis."""
    direction = 1 if zone.role == "no_short_buffer" else -1  # bullish OB -> BUY, bearish OB -> SELL
    pool = _ZONE_CISD_POOLS.get(zone.timeframe)
    if pool is None:
        return None
    for tf_minutes in pool:
        cisd = cisd_bridge.fresh_cisd(symbol, tf_minutes)
        if cisd is not None and cisd_bridge.direction_of(cisd) == direction:
            return direction, _CISD_TAG[tf_minutes], cisd
    return None


def find_ict_signals(symbol: str, block_state_file: str, eligibility: ICTEligibilityStore,
                     sl_buffer: float, bid: float, ask: float, sl_override_points: float) -> list[ICTSignal]:
    """Every currently-valid (not invalidated -- an invalidated zone is
    already deleted from the Block outright), TOUCHED, untraded OB zone
    with a fresh matching-direction CISD confirmation this cycle -- see
    module docstring for the full design, including the SL-distance
    override. Reads the Block fresh (read-only -- this component never
    writes to it) every call. bid/ask are the live prices, used only as
    the entry price for the SL-distance test."""
    store = BlockStore(block_state_file)
    signals: list[ICTSignal] = []
    cache: dict = {}

    for zone in store.zones():
        if not zone.retested:
            continue
        if eligibility.is_traded(zone.zone_id):
            continue
        dup = eligibility.overlaps_traded(zone.top, zone.btm)
        if dup is not None:
            print(f"[V6S-ICT] zone {zone.zone_id} [{zone.btm:.3f}-{zone.top:.3f}] skipped -- "
                  f"substantially overlaps already-traded zone {dup}")
            continue

        result = _check_zone(zone, symbol)
        if result is None:
            continue
        direction, trigger, cisd = result

        sl = zone.btm - sl_buffer if direction == 1 else zone.top + sl_buffer
        sl_source = "ZONE"
        entry_price = ask if direction == 1 else bid
        if abs(entry_price - sl) > sl_override_points:
            # Only a strictly TIGHTER SL replaces it (must_beat_sl) -- None means
            # nothing would reduce risk, so the zone-edge SL stays as-is.
            resolved = sl_basis.initial_sl_basis(symbol, direction, entry_price, cisd, cache,
                                                 sl_buffer=sl_buffer, must_beat_sl=sl)
            if resolved is not None:
                basis, sl_source = resolved
                sl = basis - sl_buffer if direction == 1 else basis + sl_buffer

        signals.append(ICTSignal(
            direction=direction, zone_id=zone.zone_id, timeframe_name=zone.timeframe_name,
            zone_top=zone.top, zone_btm=zone.btm, trigger=trigger, sl=sl, sl_source=sl_source,
        ))

    return signals
