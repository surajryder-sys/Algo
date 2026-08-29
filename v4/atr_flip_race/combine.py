"""Combines two independent ATR trail lines' trend readings into one
STRONG/WEAK/UNDECISIVE structure label -- same rule as
v3/tv_scraper/atr_trend_tracker.py's update_structure() (both lines
bullish -> STRONG, both bearish -> WEAK, otherwise UNDECISIVE), but
duplicated as a standalone pure function here rather than imported.

Deliberately NOT reusing AtrTrendTracker directly: that class exists to
DEBOUNCE a trend derived from live intrabar price vs trail_stop, read
fresh every scraper poll with no confirmed-bar gating at all (see its own
docstring for the real noise bug that debounce fixes). The webhook side
here is different -- trend/event_time arrive already authoritative,
computed by Pine itself exactly once per real bar close
(alert.freq_once_per_bar_close), so there is nothing to debounce; forcing
it through the same debounced-live-derivation class would be modeling a
problem this path doesn't have.
"""
from __future__ import annotations

from typing import Optional

Structure = str  # "STRONG" | "WEAK" | "UNDECISIVE"


def combine(line1_trend: Optional[int], line2_trend: Optional[int]) -> Structure:
    if line1_trend is None or line2_trend is None:
        return "UNDECISIVE"
    if line1_trend == 1 and line2_trend == 1:
        return "STRONG"
    if line1_trend == -1 and line2_trend == -1:
        return "WEAK"
    return "UNDECISIVE"
