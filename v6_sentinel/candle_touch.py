"""Shared hammer/star + HTF-line/OB-zone touch primitives. Extracted
2026-09-22 from exit_manager_candle.py (EA-CandleExit, an EXIT signal) when
the Scalper manager (scalper_entry.py, an ENTRY signal) needed the EXACT
SAME touch logic -- especially the virgin-zone eligibility-timing fix (see
virgin_as_of()'s own docstring) -- rather than risking a second,
independently-drifting copy of it.

This module only detects and reports WHICH pattern touched WHAT; it never
touches the broker or decides what to DO about a touch (close an existing
position vs open a new one) -- that's each caller's own job.

PATTERN SOURCE: bridge.read_hammer_star() -- HAMMERSTAR_<sym>_<tf>.json,
checked on M3/M5 independently (CANDLE_TIMEFRAMES). See that function's own
docstring for the "momentary but bar-scoped" contract.

LEVEL/DIRECTION PAIRING ("natural pairing", confirmed with the user
2026-09-22): a HAMMER forms at the BOTTOM of a move (long lower wick) ->
must touch an HTF SUPPORT level or a bullish/demand OB zone
("no_short_buffer") -> confirms a BULLISH signal. A STAR forms at the TOP
of a move (long upper wick) -> must touch an HTF RESISTANCE level or a
bearish/supply OB zone ("no_long_buffer") -> confirms a BEARISH signal.

HTF LINES -- SAME-CANDLE ONLY: the pattern candle's own high (star) or low
(hammer) must reach the line's value, same bar, no exception. Scope:
D1/H4/H2/H1/M30/M15/M10 (CANDLE_HTF_TIMEFRAMES) -- native for every
timeframe except M15 (bridge-only, needs a BridgeBarFlipTracker).

OB ZONES from RM-ICT's own Block (nlb_nsb_block.BlockStore, read-only) --
"only untested (virgin) zones", TIME-AWARE (virgin_as_of(), fixed
2026-09-22 after a real live miss): a zone qualifies if it was still
virgin AS OF the touching candle's own OPEN time, not whether it's still
virgin right now -- see that function's own docstring for the full "why"
(a pattern candle that is itself a zone's first-ever touch would always
show that zone as already-tested by evaluation time otherwise). The touch
itself may land on the pattern candle OR the ONE candle immediately before
it ("a previous candle could be a retest candle").
"""
from __future__ import annotations

from typing import Optional

from v6_sentinel import htf_levels
from v6_sentinel.nlb_nsb_block import BlockZone

CANDLE_TIMEFRAMES = (3, 5)  # M3, M5 -- pattern source timeframes, shared by every caller

# D1, H4, H2, H1, M30, M15, M10 -- HTF line touch scope, shared by every caller.
CANDLE_HTF_TIMEFRAMES = (1440, 240, 120, 60, 30, 15, 10)

HAMMER_ROLE, HAMMER_DIRECTION = "SUPPORT", 1
STAR_ROLE, STAR_DIRECTION = "RESISTANCE", -1
ZONE_ROLE_FOR_PATTERN = {"hammer": "no_short_buffer", "star": "no_long_buffer"}


def compute_htf_states(symbol: str, tracker) -> dict[int, Optional[htf_levels.HTFState]]:
    return {tf: htf_levels.compute_htf_state(symbol, tf, tracker) for tf in CANDLE_HTF_TIMEFRAMES}


def find_touching_line(htf_states: dict, role: str, touch_price: float) -> Optional[tuple[int, str]]:
    """(level_tf, level_source) of the first HTF line of this role touched
    by touch_price (the pattern candle's own low for SUPPORT, high for
    RESISTANCE) -- None if none qualify."""
    return next(
        (
            (level_tf, level.source)
            for level_tf, state in htf_states.items() if state is not None
            for level in state.levels
            if level.role == role
            and ((touch_price <= level.value) if role == "SUPPORT" else (touch_price >= level.value))
        ),
        None,
    )


def candle_touches_zone(high: float, low: float, zone: BlockZone) -> bool:
    """True if [low, high] overlaps the zone's own [btm, top] range -- the
    same 'price entered the range' test nlb_nsb_watcher's own live retest
    detection uses, just evaluated against one specific candle's OHLC
    instead of live ticks."""
    return low <= zone.top and high >= zone.btm


def virgin_as_of(zone: BlockZone, reference_time: int) -> bool:
    """True if this zone was still virgin at reference_time -- either it's
    never been retested at all, OR its own (live-caught) retest happened
    no EARLIER than reference_time. Fixes a real bug found live
    2026-09-22: checking zone.retested (current state) alone rejects the
    exact case this rule exists to catch -- a pattern candle that is
    ITSELF the candle performing the zone's first-ever touch will always
    show the zone as already-tested by the time that candle CLOSES and the
    pattern becomes confirmable (nlb_nsb_watcher marks it live, mid-candle,
    well before the candle's own close), so a same-candle touch could never
    pass a naive not-retested check. Using retested_at >= reference_time
    (reference_time = the touching candle's own OPEN/bar_time) correctly
    admits "this candle is what tested it" while still rejecting a zone
    that was ALREADY tested before this candle even started.
    retested_source must be "live" (not "seed" -- a scraper-history copy
    with no reliable live timestamp) for the timestamp to be trusted at
    all, same convention reversal_ict.py's own _touch_is_current() uses."""
    if not zone.retested:
        return True
    return zone.retested_source == "live" and zone.retested_at is not None and zone.retested_at >= reference_time


def find_touching_zone(zones, wanted_role: str, same_extremes: Optional[tuple[float, float]],
                       prev_extremes: Optional[tuple[float, float]], same_bar_time: int,
                       prev_bar_time: int) -> Optional[BlockZone]:
    """The first untested-as-of-its-own-touch zone of wanted_role touched
    by either the pattern candle (same_extremes/same_bar_time) or the one
    immediately before it (prev_extremes/prev_bar_time) -- None if none
    qualify. zones: an iterable of BlockZone (typically BlockStore.zones())."""
    for zone in zones:
        if zone.role != wanted_role:
            continue
        if (same_extremes is not None and virgin_as_of(zone, same_bar_time)
                and candle_touches_zone(*same_extremes, zone)):
            return zone
        if (prev_extremes is not None and virgin_as_of(zone, prev_bar_time)
                and candle_touches_zone(*prev_extremes, zone)):
            return zone
    return None
