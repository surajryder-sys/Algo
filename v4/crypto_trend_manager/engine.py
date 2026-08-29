"""Per-symbol entry decision engine -- ties together parent_bias (which
setup is winning), m5_confirm (has M5 actually confirmed it), and sl (what
stop that confirmation implies). Runs identically and independently for
BTCUSD and ETHUSD ("executions are purely based on individual
instruments") -- main.py applies BTCUSD-primary/ETHUSD-follows gating on
TOP of this module's output, not inside it.

One EngineState entry per symbol tracks which candidate is currently being
watched and whether it's already fired -- a genuinely NEW candidate (any
field of BiasCandidate.key differing, including event_time) always gets a
fresh, unfired watch; the same candidate persisting across polls while
unconfirmed just keeps checking M5 each time.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from v4.crypto_trend_manager.m5_confirm import Confirmation, check_confirmation
from v4.crypto_trend_manager.parent_bias import BiasCandidate, winning_candidate
from v4.crypto_trend_manager.sl import ict_sl, str_sl
from v4.crypto_trend_manager.tv_reader import (
    MAX_SCRAPER_AGE_SECONDS,
    is_scraper_alive,
    read_latest_ob,
    read_structure,
    read_trail_stops,
)

Direction = Literal["buy", "sell"]

_PARENT_MINUTES = {"M30": 30, "M15": 15}


@dataclass
class Decision:
    direction: Direction
    sl: float
    candidate: BiasCandidate
    confirm: Confirmation
    comment_tag: str  # e.g. "M15-ICT-M5-STR"


@dataclass
class EvaluationResult:
    decision: Optional[Decision]
    reason: str


class EngineState:
    def __init__(self, path: str):
        self._path = Path(path)
        # symbol -> {"candidate_key": [parent, kind, direction, event_time] or None, "fired": bool}
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._data = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            self._data = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._data, indent=2))

    def _entry(self, symbol: str) -> dict:
        return self._data.setdefault(symbol, {"candidate_key": None, "fired": False})

    def current_key(self, symbol: str) -> Optional[list]:
        return self._entry(symbol)["candidate_key"]

    def is_fired(self, symbol: str) -> bool:
        return self._entry(symbol)["fired"]

    def adopt(self, symbol: str, key: Optional[tuple]) -> None:
        self._entry(symbol)["candidate_key"] = list(key) if key is not None else None
        self._entry(symbol)["fired"] = False
        self._save()

    def mark_fired(self, symbol: str) -> None:
        self._entry(symbol)["fired"] = True
        self._save()


def evaluate_symbol(state: EngineState, symbol: str) -> EvaluationResult:
    if not is_scraper_alive(symbol):
        return EvaluationResult(None, f"{symbol}'s tv_scraper looks dead/stale (no live snapshot update in the "
                                       f"last {MAX_SCRAPER_AGE_SECONDS:.0f}s) -- skipping this poll rather than "
                                       f"trusting stale data")

    winner = winning_candidate(symbol)
    if winner is None:
        if state.current_key(symbol) is not None:
            state.adopt(symbol, None)
        return EvaluationResult(None, "no parent bias candidate (M30/M15 both UNDECISIVE with no live OB zone)")

    if list(winner.key) != state.current_key(symbol):
        state.adopt(symbol, winner.key)

    if state.is_fired(symbol):
        return EvaluationResult(None, f"{winner.tag} {winner.direction} already fired -- waiting for a new candidate")

    m5 = read_structure(symbol, 5)
    if m5 is None:
        return EvaluationResult(None, f"{winner.tag} {winner.direction} winning (et={winner.event_time}) "
                                       f"but M5 bridge unavailable this poll")

    confirm = check_confirmation(m5, winner.direction)
    if confirm is None:
        return EvaluationResult(None, f"{winner.tag} {winner.direction} winning (et={winner.event_time}), "
                                       f"waiting for M5 confirmation (M5 structure={m5.state})")

    if confirm.event_time <= winner.event_time:
        return EvaluationResult(None, f"{winner.tag} {winner.direction} winning but M5's {confirm.kind} "
                                       f"confirmation (et={confirm.event_time}) predates the parent bias itself "
                                       f"(et={winner.event_time}) -- stale, waiting for a fresh M5 flip")

    sl = _compute_sl(symbol, winner)
    if sl is None:
        return EvaluationResult(None, f"{winner.tag} {winner.direction} confirmed by M5 ({confirm.kind}) but "
                                       f"SL inputs unavailable this poll (zone/trail data missing) -- waiting")

    state.mark_fired(symbol)
    decision = Decision(direction=winner.direction, sl=sl, candidate=winner, confirm=confirm,
                         comment_tag=f"{winner.tag}-M5-STR")
    return EvaluationResult(decision, f"FIRING {winner.direction} via {winner.tag} -- M5 {confirm.kind} "
                                       f"confirmation (et={confirm.event_time}) after parent bias (et={winner.event_time})")


def _compute_sl(symbol: str, candidate: BiasCandidate) -> Optional[float]:
    minutes = _PARENT_MINUTES[candidate.parent]
    if candidate.kind == "ICT":
        zone = read_latest_ob(symbol, minutes)
        # Must still be the SAME zone the candidate was derived from -- if
        # it's been mitigated (or superseded by an even newer one) since
        # winning_candidate() last ran, there's nothing to base an ICT SL
        # on; the caller's next poll will naturally pick up whatever the
        # new winning candidate is instead.
        if zone is None or zone.start_time != candidate.event_time:
            return None
        return ict_sl(symbol, candidate.direction, zone.top, zone.btm)

    trails = read_trail_stops(symbol, minutes)
    if trails is None:
        return None
    return str_sl(symbol, candidate.direction, trails[0], trails[1])
