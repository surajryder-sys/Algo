"""Races the webhook (push) and tv_scraper (pull) M1 ATR-structure sources
against each other, per symbol, purely to observe and log which one
confirms a given flip first -- this package makes NO trading decisions
and places no orders (explicit scope, 2026-08-29: "just detection for
now" -- BTC/ETH have no live execution engine at all right now).

Both sources are eventually consistent on the SAME real flip (same
structure_event_time) -- the only question this answers is which one's
local pipeline gets there first, and by how much, so that question can be
answered with real numbers instead of guessing. See webhook_ingest.py and
scraper_read.py for how each source's current reading is produced.

Resolution rule per symbol, evaluated every poll:
  - Compare each source's CURRENT reading (structure, event_time) against
    what it reported last poll (own history) to see whether it just moved.
  - A source's move only counts as a new race if it doesn't just match
    whatever is already resolved (the runner-up catching up to an
    already-decided flip is a confirmation, not a new race -- logged once).
  - Exactly one source moving-to-something-new this poll -> that source
    wins, resolved immediately.
  - Both moving to the SAME new value in the same poll -> a tie (this
    poll's granularity can't separate them).
  - Both moving to DIFFERENT new values in the same poll -> genuine
    disagreement; wait rather than guess (same principle used everywhere
    else in this codebase for conflicting sources -- see
    v4/trend_manager/m1_execution.py's own docstring), left unresolved
    until they agree.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from v4.atr_flip_race.scraper_read import read_scraper_structure
from v4.atr_flip_race.webhook_ingest import WebhookIngestor

SYMBOLS = ("BTCUSD", "ETHUSD")


@dataclass
class _SourceReading:
    state: Optional[str] = None
    event_time: Optional[int] = None

    def as_tuple(self) -> tuple[Optional[str], Optional[int]]:
        return self.state, self.event_time


@dataclass
class _Resolved:
    state: Optional[str] = None
    event_time: Optional[int] = None
    winner: Optional[str] = None          # "webhook" | "scraper" | "tie"
    resolved_at: Optional[float] = None   # wall-clock, for gap measurement
    confirmed_by_other_at: Optional[float] = None  # None until logged once


class RaceState:
    def __init__(self, path: str, log_file: str):
        self._path = Path(path)
        self.webhook = WebhookIngestor(log_file=log_file)
        self._prev: dict[str, dict[str, _SourceReading]] = {
            sym: {"webhook": _SourceReading(), "scraper": _SourceReading()} for sym in SYMBOLS
        }
        self.resolved: dict[str, _Resolved] = {sym: _Resolved() for sym in SYMBOLS}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        self.webhook.cursor = raw.get("cursor", 0)
        for sym in SYMBOLS:
            sd = raw.get("symbols", {}).get(sym, {})
            wh = sd.get("webhook", {})
            self.webhook._symbols[sym] = type(self.webhook._sym(sym))(
                line1_trend=wh.get("line1_trend"), line2_trend=wh.get("line2_trend"),
                structure=wh.get("structure", "UNDECISIVE"), structure_event_time=wh.get("structure_event_time"),
            )
            self._prev[sym]["webhook"] = _SourceReading(**sd.get("prev_webhook", {}))
            self._prev[sym]["scraper"] = _SourceReading(**sd.get("prev_scraper", {}))
            r = sd.get("resolved", {})
            self.resolved[sym] = _Resolved(**r) if r else _Resolved()

    def save(self) -> None:
        out = {"cursor": self.webhook.cursor, "symbols": {}}
        for sym in SYMBOLS:
            wh = self.webhook._sym(sym)
            out["symbols"][sym] = {
                "webhook": {"line1_trend": wh.line1_trend, "line2_trend": wh.line2_trend,
                            "structure": wh.structure, "structure_event_time": wh.structure_event_time},
                "prev_webhook": vars(self._prev[sym]["webhook"]),
                "prev_scraper": vars(self._prev[sym]["scraper"]),
                "resolved": vars(self.resolved[sym]),
            }
        self._path.write_text(json.dumps(out, indent=2))


def poll_once(state: RaceState, now: Optional[float] = None) -> list[str]:
    """Runs one race iteration for every symbol; returns human-readable log
    lines for whatever happened (empty list on a fully quiet poll -- no
    new source movement at all, nothing to report)."""
    now = time.time() if now is None else now
    state.webhook.poll()
    lines: list[str] = []

    for sym in SYMBOLS:
        wh_state, wh_et = state.webhook.current(sym)
        wh_reading = _SourceReading(wh_state, wh_et)

        sc = read_scraper_structure(sym)
        sc_reading = _SourceReading(*sc) if sc is not None else state._prev[sym]["scraper"]

        prev_wh = state._prev[sym]["webhook"]
        prev_sc = state._prev[sym]["scraper"]
        resolved = state.resolved[sym]

        wh_moved = wh_reading.as_tuple() != prev_wh.as_tuple()
        sc_moved = sc_reading.as_tuple() != prev_sc.as_tuple()

        # A source with event_time=None has never produced a single real M1
        # reading (webhook: no TradingView Alert configured for M1 yet on
        # this symbol's chart -- see this module's own docstring). That's
        # "nothing to race" for that source, not a genuine disagreement --
        # without this gate, cold start (or any period before the M1 alert
        # exists) would misreport every poll as DISAGREEMENT purely because
        # webhook's permanent UNDECISIVE/None doesn't match whatever the
        # scraper legitimately sees.
        wh_has_data = wh_reading.event_time is not None
        sc_has_data = sc_reading.event_time is not None

        wh_is_new_race = wh_has_data and wh_moved and wh_reading.as_tuple() != (resolved.state, resolved.event_time)
        sc_is_new_race = sc_has_data and sc_moved and sc_reading.as_tuple() != (resolved.state, resolved.event_time)

        if wh_is_new_race and sc_is_new_race:
            if wh_reading.as_tuple() == sc_reading.as_tuple():
                resolved.state, resolved.event_time = wh_reading.state, wh_reading.event_time
                resolved.winner, resolved.resolved_at, resolved.confirmed_by_other_at = "tie", now, now
                lines.append(f"[{sym}] TIE -- webhook and scraper both confirmed {wh_reading.state} "
                             f"(event_time={wh_reading.event_time}) in the same poll cycle")
            else:
                lines.append(f"[{sym}] DISAGREEMENT -- webhook says {wh_reading.state}"
                             f"(et={wh_reading.event_time}), scraper says {sc_reading.state}"
                             f"(et={sc_reading.event_time}) -- waiting, not resolving")
        elif wh_is_new_race:
            resolved.state, resolved.event_time = wh_reading.state, wh_reading.event_time
            resolved.winner, resolved.resolved_at, resolved.confirmed_by_other_at = "webhook", now, None
            lines.append(f"[{sym}] WEBHOOK wins -- confirmed {wh_reading.state} first "
                         f"(event_time={wh_reading.event_time})")
        elif sc_is_new_race:
            resolved.state, resolved.event_time = sc_reading.state, sc_reading.event_time
            resolved.winner, resolved.resolved_at, resolved.confirmed_by_other_at = "scraper", now, None
            lines.append(f"[{sym}] SCRAPER wins -- confirmed {sc_reading.state} first "
                         f"(event_time={sc_reading.event_time})")
        elif resolved.confirmed_by_other_at is None and resolved.winner is not None:
            other = sc_reading if resolved.winner == "webhook" else wh_reading
            other_name = "scraper" if resolved.winner == "webhook" else "webhook"
            if other.as_tuple() == (resolved.state, resolved.event_time):
                gap = now - resolved.resolved_at
                resolved.confirmed_by_other_at = now
                lines.append(f"[{sym}] {other_name} confirmed the same flip {gap:.1f}s after "
                             f"{resolved.winner} won (state={resolved.state})")

        state._prev[sym]["webhook"] = wh_reading
        state._prev[sym]["scraper"] = sc_reading

    state.save()
    return lines
