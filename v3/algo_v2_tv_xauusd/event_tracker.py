"""Diffs each poll's zone/ATR snapshots against what was seen last poll,
and logs every NEW fact (zone formed/retested/mitigated, raw ATR flip,
combined per-timeframe bias flip) to EventLog exactly once. v1 scope:
observation only -- nothing here feeds back into trading decisions yet
(see algo_v2_tv_xauusd/main.py's own v1-scope note); the log this builds
is what later execution logic will read.

Startup behavior worth knowing: reader.read_zone()'s merged view keeps
EVERY zone ever seen (never pruned -- see reader.py's own docstring), so
on this tracker's first poll after a (re)start, every zone already in that
history logs as ob_formed even though most aren't actually new. Each
record's detected_time (the zone's true origin) vs recorded_at (when this
process first logged it) makes that startup backfill distinguishable from
a genuine live detection after the fact -- a large gap between the two
means "seen at startup," not "just happened."

Per-timeframe bias reuses zone.compute_zone() exactly as algo_v2 already
does for the single M5-derived "official" bias (see that function's own
docstring) -- just run once per timeframe here instead of only for M5,
using each timeframe's own ATR reading and own OB snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from v3.algo_v2_tv_xauusd.active_events import ActiveEventStore
from v3.algo_v2_tv_xauusd.event_log import EventLog
from v3.algo_v2_tv_xauusd.reader import ATRSnapshot, OBSnapshot, Zone
from v3.algo_v2_tv_xauusd.zone import ZoneState, compute_zone


@dataclass
class _TimeframeState:
    seen_zones: dict = field(default_factory=dict)  # start_time -> last-seen Zone
    atr_trend: Optional[int] = None
    bias_state: Optional[ZoneState] = None


class EventTracker:
    def __init__(self, log: EventLog, symbol: str, active: Optional[ActiveEventStore] = None):
        self._log = log
        self._symbol = symbol
        # Optional -- v1 callers that only want the audit trail (EventLog)
        # can omit this and get the exact same behavior as before it
        # existed. See active_events.py's own docstring for what this adds.
        self._active = active
        self._tf_state: dict[str, _TimeframeState] = {}

    def _state_for(self, tf: str) -> _TimeframeState:
        return self._tf_state.setdefault(tf, _TimeframeState())

    def observe_zones(self, tf: str, direction: str, zones: list[Zone]) -> None:
        st = self._state_for(tf)
        for z in zones:
            # Keyed by (direction, start_time), NOT start_time alone --
            # confirmed live: a bull and a bear zone can share the exact
            # same start_time (tv_scraper's "first observed" timestamps
            # are whole-second granularity, and both directions can get
            # first-observed in the same poll), and seen_zones is shared
            # across both direction calls for this same _TimeframeState.
            # A start_time-only key let an unrelated opposite-direction
            # zone masquerade as this one's "previous" state, firing false
            # ob_retested/ob_mitigated events for zones that were actually
            # brand new.
            key = (direction, z.start_time)
            prev = st.seen_zones.get(key)
            if prev is None:
                self._log.ob_formed(self._symbol, tf, direction, z.start_time,
                                    z.high, z.low, z.detected_time, z.detected_price)
                # Skip the active store specifically if this "first
                # sighting" is really just startup backfill from
                # ZoneStore's full, never-pruned history (see reader.py's
                # own docstring) for a zone that was ALREADY mitigated
                # before this process ever saw it -- confirmed live
                # (BTCUSD/M1): a cold start added 38 stale historic zones
                # to the active store in one shot, none of which were
                # still on the chart, and none would ever be removed since
                # removal only fires on a live not-mitigated -> mitigated
                # TRANSITION, which a zone that's already mitigated on
                # first sight never produces. EventLog still logs the
                # ob_formed fact unconditionally above -- this only guards
                # the "currently live" store.
                if self._active is not None and z.mitigated_time is None:
                    self._active.add(self._symbol, tf, direction, z.start_time,
                                     z.high, z.low, z.detected_time, z.detected_price,
                                     retested_at=z.retested_at)
            else:
                if prev.retested_at is None and z.retested_at is not None:
                    self._log.ob_retested(self._symbol, tf, direction, z.start_time, z.retested_at)
                    if self._active is not None:
                        self._active.mark_retested(self._symbol, tf, direction, z.start_time, z.retested_at)
                if prev.mitigated_time is None and z.mitigated_time is not None:
                    self._log.ob_mitigated(self._symbol, tf, direction, z.start_time)
                    if self._active is not None:
                        self._active.remove(self._symbol, tf, direction, z.start_time)
            st.seen_zones[key] = z

    def observe_atr(self, tf: str, atr: Optional[ATRSnapshot]) -> None:
        if atr is None:
            return
        st = self._state_for(tf)
        if st.atr_trend is not None and st.atr_trend != atr.trend:
            self._log.atr_flip(self._symbol, tf, atr.trend, atr.event_time)
        st.atr_trend = atr.trend

    def observe_bias(self, tf: str, atr: Optional[ATRSnapshot], ob: Optional[OBSnapshot]) -> None:
        result = compute_zone(atr, ob)
        if result.state == ZoneState.NONE:
            return
        st = self._state_for(tf)
        if st.bias_state is not None and st.bias_state != result.state:
            self._log.bias_changed(self._symbol, tf, result.state.value, result.event_time)
        st.bias_state = result.state
