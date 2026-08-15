"""Order Block + ATR Trail zone rules -- identical to algo_v2/zone.py,
copied with only the OBSnapshot/ATRSnapshot import swapped for this bot's
own TradingView-data adapter (algo_v2_tv_xauusd.reader). The rule itself is
data-source-agnostic: it only ever reads .bull/.bear/.event_time/.trend,
which reader.py's OBSnapshot/ATRSnapshot expose the same way ob_bridge/
atr_bridge's do.

The current "effective direction" and its event_time boundary aren't just
the ATR Trail's Strong/Weak label -- they're the most recent of THREE
competing signals, all M5-based:
  1. M5 forming its own fresh bullish OB (origin candle time)
  2. M5 forming its own fresh bearish OB (origin candle time)
  3. the ATR Trail's last Strong<->Weak flip

Whichever of those three timestamps is the most recent wins: its direction
becomes the effective direction (STRONG=bullish-favored, WEAK=bearish-
favored), and its timestamp becomes the event_time boundary every OB
candidate gets checked against.

Eligibility rule for M3 and M5:
  Direction matching the effective direction -- always eligible, old or
  new.
  Direction opposite the effective direction -- eligible only if that
  specific OB's own event time is at/after the event_time boundary.
  No effective direction yet (no data at all) -- nothing eligible; fail
  closed rather than silently ignoring the safeguard.

M1 uses the same function with strict=True: see algo_v2/zone.py's
docstring for the full rationale (unchanged here).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from algo_v2_tv_xauusd.reader import ATRSnapshot, OBSnapshot


class ZoneState(Enum):
    NONE = "NONE"
    STRONG = "STRONG"
    WEAK = "WEAK"


@dataclass(frozen=True)
class ZoneResult:
    state: ZoneState     # STRONG = effective direction bullish, WEAK = bearish, NONE = no data yet
    event_time: int      # the winning (most recent) of the three source timestamps


def _m5_ob_time(m5: Optional[OBSnapshot], direction: int) -> int:
    """M5's own latest OB origin time in this direction, 0 if none. Uses
    start_time (the OB's origin candle), not detected_time -- same
    stability reasoning as algo_v2/zone.py.

    Skips already-mitigated zones when picking "the latest" -- confirmed
    live (M3, chart screenshot): a zone that mitigates within ~30s of its
    own start_time is not real price action (a candle can't both form AND
    break its own zone within a fraction of one bar on M3+), it's
    reader.py's known tv_scraper-source artifact (a zone briefly dropping
    out of the indicator's top-4 display slots and churning back in under
    a fresh start_time -- see reader.py's and scraper.py's own comments).
    Letting a mitigated-in-30-seconds zone win compute_zone()'s "most
    recent" comparison flipped M3's bias STRONG on a phantom formation
    that was never actually visible on the real chart. algo_v2's original
    (real MT5 bar-time) data can't produce this pattern, but this copy's
    tv_scraper-backed data can, so this guard is specific to this file."""
    if m5 is None:
        return 0
    history = m5.bull if direction == 1 else m5.bear
    for zone in history:
        if zone.mitigated_time is None:
            return zone.start_time
    return 0


def compute_zone(atr: Optional[ATRSnapshot], m5: Optional[OBSnapshot]) -> ZoneResult:
    """Combines the ATR Trail flip with M5's own latest bullish/bearish OB
    times -- whichever of the three is most recent sets the effective
    direction and event_time boundary."""
    candidates = []  # (event_time, direction)

    if atr is not None:
        candidates.append((atr.event_time, 1 if atr.trend > 0 else -1))

    bull_time = _m5_ob_time(m5, 1)
    bear_time = _m5_ob_time(m5, -1)

    # A single bar can never form both a bullish AND a bearish OB -- `os`
    # (the origin condition each direction's get_coordinates() call gates
    # on) is one mutually-exclusive state, so bull_time == bear_time is
    # structurally impossible in the real underlying bar-time data. It
    # DOES happen here though, because this reader's timestamps come from
    # tv_scraper's own "wall-clock time first observed this poll"
    # approximation (see reader.py's Zone.start_time docstring), and two
    # genuinely different-bar zones can easily land in the same poll
    # cycle. Confirmed live: H1 and M30 both showed bull/bear start_time
    # identical to the second, and since Python's max() keeps whichever
    # candidate it saw FIRST on an exact tie, bull always silently won --
    # producing a STRONG bias with no real bullish signal behind it, on
    # every tie. Since neither can be trusted as "more recent" when tied,
    # drop both rather than let list order decide, and fall back to
    # ATR/whichever single OB signal remains.
    tied = bull_time > 0 and bull_time == bear_time
    if not tied:
        if bull_time > 0:
            candidates.append((bull_time, 1))
        if bear_time > 0:
            candidates.append((bear_time, -1))

    if not candidates:
        return ZoneResult(ZoneState.NONE, 0)

    event_time, direction = max(candidates, key=lambda c: c[0])
    state = ZoneState.STRONG if direction == 1 else ZoneState.WEAK
    return ZoneResult(state, event_time)


def is_eligible(zone: ZoneResult, direction: int, ob_event_time: int, strict: bool = False) -> bool:
    """direction: 1 bullish, -1 bearish. ob_event_time: the candidate OB's
    own detected/origin time (matches candidates.py's _event_time).
    strict: M1 only -- drops the "opposite but newer" exception below;
    the candidate's direction must equal the effective direction, full
    stop. M3/M5 always call this with strict=False (the default)."""
    if zone.state == ZoneState.NONE:
        return False

    favored = 1 if zone.state == ZoneState.STRONG else -1
    if direction == favored:
        return True

    if strict:
        return False

    # Opposite-of-effective-direction: only eligible if it formed at/after
    # the current event_time boundary -- older ones stay blocked.
    return ob_event_time >= zone.event_time
