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
D1/H4/H2/H1/M30/M15/M10/M5 (CANDLE_HTF_TIMEFRAMES, M5 added 2026-09-23) --
native for every timeframe except M15 and M5 (bridge-only, needs a
BridgeBarFlipTracker). ALSO
(added 2026-09-23) that same candle's own CLOSE must stay on the correct
side of the line -- see find_touching_line()'s own docstring for the
full "why" (a wick that reaches a line but then closes past it is a clean
break, not a genuine touch/rejection, and no longer a valid reference).

OB ZONES from RM-ICT's own Block (nlb_nsb_block.BlockStore, read-only) --
"only untested (virgin) zones", TIME-AWARE (virgin_as_of(), fixed
2026-09-22 after a real live miss): a zone qualifies if it was still
virgin AS OF the touching candle's own OPEN time, not whether it's still
virgin right now -- see that function's own docstring for the full "why"
(a pattern candle that is itself a zone's first-ever touch would always
show that zone as already-tested by evaluation time otherwise). The touch
itself may land on the pattern candle OR the ONE candle immediately before
it ("a previous candle could be a retest candle").

PATTERN-FLAG RESILIENCE (is_hammer()/is_star(), added 2026-09-23 after a
real live inconsistency): the bridge's momentary hammer/star booleans can
transiently read False for a bar its OWN last_hammer_time/last_star_time
already confirms was that shape -- observed live 2026-09-22, 3 separate
polls in a row, star=False while last_star_time exactly equalled bar_time.
Root cause (mql5/ATR_Trial_Dual_SuperTrend_Major_Minor_HammerStar.mq5's own
OnCalculate reprocessing loop): last_hammer_time/last_star_time update
whenever ANY bar in the current reprocess range evaluates true, while the
published hammer_now/star_now only ever reflects the LAST bar in that
range -- for last_star_time to equal bar_time (the current last-closed
bar) while star reads False, the SAME bar index must have evaluated True
on one OnCalculate pass and False on a LATER pass with hs_last_closed
still pointing at it (no new bar has closed since). HS_EvaluateBar() is a
pure function of a bar's own OHLC plus its 10-bar HS_LOOKBACK window, so
the only way the SAME bar's result can change between two passes is if the
underlying rate data itself was revised in between -- a known (if
uncommon) MT5 behavior where a broker feed corrects very-recently-closed
bar data shortly after it first appears closed. Not an indicator logic
bug; an inherent risk of evaluating pattern shape against a feed that can
still revise itself. is_hammer()/is_star() below are the resilient check
every caller should use instead of hs.hammer/hs.star directly -- they also
trust last_*_time whenever it points at the EXACT current bar (a stronger
condition than "recently set" -- an older bar's last_*_time can never
false-positive-match a different, newer bar_time).
"""
from __future__ import annotations

from typing import Optional

from v7_sentinel import htf_levels
from v7_sentinel.bridge import HammerStar
from v7_sentinel.nlb_nsb_block import BlockZone

CANDLE_TIMEFRAMES = (3, 5)  # M3, M5 -- pattern source timeframes, shared by every caller

# D1, H4, H2, H1, M30, M15, M10, M5 -- HTF line touch scope, shared by every caller. M5 added
# 2026-09-23 (user: "add m5 as well on HTF support and resistance") -- bridge-only (see
# bridge.BRIDGE_ONLY_TIMEFRAMES), same as M15, already fully supported by htf_levels.compute_htf_state.
CANDLE_HTF_TIMEFRAMES = (1440, 240, 120, 60, 30, 15, 10, 5)

HAMMER_ROLE, HAMMER_DIRECTION = "SUPPORT", 1
STAR_ROLE, STAR_DIRECTION = "RESISTANCE", -1
ZONE_ROLE_FOR_PATTERN = {"hammer": "no_short_buffer", "star": "no_long_buffer"}


def is_hammer(hs: HammerStar) -> bool:
    """True if the CURRENT last-closed bar (hs.bar_time) is a hammer -- see
    module docstring's PATTERN-FLAG RESILIENCE section for why this is
    hs.hammer OR (hs.last_hammer_time == hs.bar_time), not hs.hammer alone."""
    return hs.hammer or hs.last_hammer_time == hs.bar_time


def is_star(hs: HammerStar) -> bool:
    """See is_hammer()'s own docstring -- same resilience, star side."""
    return hs.star or hs.last_star_time == hs.bar_time


def compute_htf_states(symbol: str, tracker) -> dict[int, Optional[htf_levels.HTFState]]:
    return {tf: htf_levels.compute_htf_state(symbol, tf, tracker) for tf in CANDLE_HTF_TIMEFRAMES}


def find_touching_line(htf_states: dict, role: str, touch_price: float, close_price: float) -> Optional[tuple[int, str]]:
    """(level_tf, level_source) of the first HTF line of this role touched
    by touch_price (the pattern candle's own low for SUPPORT, high for
    RESISTANCE) -- None if none qualify.

    ALSO requires close_price (that SAME candle's own close) to sit on the
    CORRECT side of the line -- above it for SUPPORT, below it for
    RESISTANCE (added 2026-09-23, user: "any level becomes valid to close
    only if qualifying exit is above the support price line, likewise
    qualifying close should be under resistance"). A wick that merely
    reaches a line isn't enough on its own -- if the candle actually
    CLOSED past the line (a clean break, not a rejection/bounce), that
    line no longer counts as a meaningful support/resistance reference for
    this candle, regardless of how far the wick reached. Same "closed
    candle, not just a wick" validity principle already used for SL-line
    usability elsewhere in this project (a line must sit on the correct
    side of both the entry price and the last closed candle)."""
    return next(
        (
            (level_tf, level.source)
            for level_tf, state in htf_states.items() if state is not None
            for level in state.levels
            if level.role == role
            and ((touch_price <= level.value) if role == "SUPPORT" else (touch_price >= level.value))
            and ((close_price > level.value) if role == "SUPPORT" else (close_price < level.value))
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
