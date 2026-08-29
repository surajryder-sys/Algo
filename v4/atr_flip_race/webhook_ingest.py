"""Turns raw atr_trail webhook events (one per ATR line, per bar close --
see pine/OBD_ATR.pine's two separate alert() calls) into one running
combined structure reading per symbol, mirroring what
v3/tv_scraper/atr_trend_tracker.py's update_structure() computes on the
scraper side -- except here both lines' trend/event_time already arrive
authoritative from Pine itself (real bar-close values, not derived from
live intrabar price), so there's nothing to debounce, just combine.

Tails v3/tv_bridge's shared signal log (the same file every webhook type
lands in, from every symbol/timeframe with an alert configured) via its
own persisted byte cursor -- same read-only, restart-safe contract
v3.tv_bridge.reader.read_new already documents.

Lives under v4/ (moved 2026-08-29, was briefly v3/atr_flip_race) since
this is BTCUSD/ETHUSD's own execution-signal detection layer for the V4
lineage, not v3 -- but still imports v3.tv_bridge.reader directly, same
narrow exception v4/bridge/reader.py already sets precedent for (reusing
ob_bridge.reader.bridge_root() rather than duplicating it): tv_bridge's
log-tailing is generic, symbol-agnostic plumbing with no bot-specific
logic in it, not something worth forking a second copy of.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from v3.tv_bridge.reader import read_new

from v4.atr_flip_race.combine import combine

# Matches pine/OBD_ATR.pine's atrperiod_1 (fast) / atrperiod_2 (slow) input
# defaults -- the payload's own "atr_period" field is whatever the chart's
# actual input values are, so this only holds if both charts are still on
# the default 2/300 split. Confirmed live 2026-08-29 against the running
# BTCUSD/ETHUSD webhook data (see this session's own log inspection).
_ATR_PERIOD_TO_LINE = {"2": "line1", "300": "line2"}

# M1 only -- this whole package exists to race M1 execution signals; other
# timeframes' atr_trail events pass through the same log but are ignored
# here (buffer-zone timeframes have no execution urgency to race).
_TIMEFRAME = "1"


@dataclass
class _SymbolLines:
    line1_trend: Optional[int] = None
    line2_trend: Optional[int] = None
    structure: str = "UNDECISIVE"
    structure_event_time: Optional[int] = None


@dataclass
class WebhookIngestor:
    """Owns the log cursor and per-symbol combined state. Call poll() once
    per race loop iteration; it never blocks and never raises on a missing/
    mid-write log file (same transient-tolerance contract as every other
    bridge reader in this repo)."""
    log_file: str
    cursor: int = 0
    _symbols: dict[str, _SymbolLines] = field(default_factory=dict)

    def _sym(self, symbol: str) -> _SymbolLines:
        return self._symbols.setdefault(symbol, _SymbolLines())

    def current(self, symbol: str) -> tuple[str, Optional[int]]:
        """Current combined reading for a symbol without consuming new
        events -- (structure, structure_event_time), UNDECISIVE/None if
        nothing's arrived for it yet."""
        s = self._sym(symbol)
        return s.structure, s.structure_event_time

    def poll(self) -> None:
        """Consumes every new atr_trail/M1 event since the last poll and
        updates each affected symbol's combined structure in place. Does
        NOT return what changed -- callers compare current() against their
        own previous snapshot (see race.py), so this stays a pure ingest
        step with no opinion on what counts as "new" for racing purposes."""
        events, self.cursor = read_new(self.log_file, self.cursor)
        for ev in events:
            if ev.type != "atr_trail" or ev.data.get("timeframe") != _TIMEFRAME:
                continue
            line = _ATR_PERIOD_TO_LINE.get(str(ev.data.get("atr_period")))
            if line is None:
                continue
            trend = ev.data.get("trend")
            event_time = ev.data.get("event_time")
            if trend is None or event_time is None:
                continue

            s = self._sym(ev.symbol)
            setattr(s, f"{line}_trend", int(trend))
            new_structure = combine(s.line1_trend, s.line2_trend)
            if new_structure != s.structure:
                s.structure = new_structure
                # The triggering event's OWN event_time -- already a real
                # bar timestamp from Pine, no local wall-clock stand-in
                # needed (unlike the scraper side, which has none of its
                # own -- see atr_trend_tracker.py's docstring).
                s.structure_event_time = int(event_time)
