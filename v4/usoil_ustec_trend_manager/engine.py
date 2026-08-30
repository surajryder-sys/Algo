"""Per-symbol entry decision engine -- identical logic to
crypto_trend_manager's own engine.py (structure-only, post-ICT-removal
shape): ties together parent_bias (which setup is winning), m5_confirm
(has M5 actually confirmed it), and sl (what stop that confirmation
implies). Runs identically and independently for USOIL and USTEC (no
cross-symbol gating at all -- explicit user choice, unlike BTCUSD/
ETHUSD's primary/secondary relationship).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from v4.usoil_ustec_trend_manager.m5_confirm import Confirmation, check_confirmation
from v4.usoil_ustec_trend_manager.parent_bias import BiasCandidate, winning_candidate
from v4.usoil_ustec_trend_manager.sl import str_sl
from v4.usoil_ustec_trend_manager.tv_reader import (
    MAX_SCRAPER_AGE_SECONDS,
    is_scraper_alive,
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
    comment_tag: str  # e.g. "V4S-M15-STR-M5-STR"


@dataclass
class EvaluationResult:
    decision: Optional[Decision]
    reason: str


class EngineState:
    def __init__(self, path: str):
        self._path = Path(path)
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
        entry = self._data.setdefault(symbol, {})
        entry.setdefault("candidate_key", None)
        entry.setdefault("fired", False)
        entry.setdefault("last_confirmation_event_time", None)
        return entry

    def current_key(self, symbol: str) -> Optional[list]:
        return self._entry(symbol)["candidate_key"]

    def is_fired(self, symbol: str) -> bool:
        return self._entry(symbol)["fired"]

    def last_confirmation_event_time(self, symbol: str) -> Optional[int]:
        return self._entry(symbol).get("last_confirmation_event_time")

    def adopt(self, symbol: str, key: Optional[tuple]) -> None:
        self._entry(symbol)["candidate_key"] = list(key) if key is not None else None
        self._entry(symbol)["fired"] = False
        self._save()

    def mark_fired(self, symbol: str, confirmation_event_time: int) -> None:
        self._entry(symbol)["last_confirmation_event_time"] = confirmation_event_time
        self._entry(symbol)["fired"] = True
        self._save()


def evaluate_symbol(state: EngineState, symbol: str, current_position_direction: Optional[str] = None) -> EvaluationResult:
    if not is_scraper_alive():
        return EvaluationResult(None, f"USOIL/USTEC's shared tv_scraper looks dead/stale (no live snapshot "
                                       f"update in the last {MAX_SCRAPER_AGE_SECONDS:.0f}s) -- skipping this poll")

    winner = winning_candidate(symbol)
    if winner is None:
        if state.current_key(symbol) is not None:
            state.adopt(symbol, None)
        return EvaluationResult(None, "no parent bias candidate (M30/M15 both UNDECISIVE, no directional flip yet)")

    if list(winner.key) != state.current_key(symbol):
        state.adopt(symbol, winner.key)

    if state.is_fired(symbol):
        return EvaluationResult(None, f"{winner.tag} {winner.direction} already fired -- waiting for a new candidate")

    if current_position_direction == winner.direction:
        return EvaluationResult(None, f"{winner.tag} {winner.direction} winning but {symbol} is already "
                                       f"positioned {winner.direction} -- not stacking a duplicate")

    m5 = read_structure(symbol, 5)
    if m5 is None:
        return EvaluationResult(None, f"{winner.tag} {winner.direction} winning (et={winner.event_time}) "
                                       f"but M5 bridge unavailable this poll")

    confirm = check_confirmation(m5, winner.direction)
    if confirm is None:
        return EvaluationResult(None, f"{winner.tag} {winner.direction} winning (et={winner.event_time}), "
                                       f"waiting for M5 confirmation (M5 structure={m5.state})")

    if confirm.event_time < winner.event_time:
        return EvaluationResult(None, f"{winner.tag} {winner.direction} winning but M5's {confirm.kind} "
                                       f"confirmation (et={confirm.event_time}) predates the parent bias itself "
                                       f"(et={winner.event_time}) -- stale, waiting for a fresh M5 flip")

    last_used = state.last_confirmation_event_time(symbol)
    if last_used is not None and confirm.event_time <= last_used:
        return EvaluationResult(None, f"{winner.tag} {winner.direction} winning but its M5 {confirm.kind} "
                                       f"confirmation (et={confirm.event_time}) already fired an earlier trade "
                                       f"(last used et={last_used}) -- not a fresh flip, waiting for a new one")

    sl = _compute_sl(symbol, winner)
    if sl is None:
        return EvaluationResult(None, f"{winner.tag} {winner.direction} confirmed by M5 ({confirm.kind}) but "
                                       f"SL inputs unavailable this poll (trail data missing) -- waiting")

    state.mark_fired(symbol, confirm.event_time)
    decision = Decision(direction=winner.direction, sl=sl, candidate=winner, confirm=confirm,
                         comment_tag=f"V4S-{winner.tag}-M5-STR")
    return EvaluationResult(decision, f"FIRING {winner.direction} via {winner.tag} -- M5 {confirm.kind} "
                                       f"confirmation (et={confirm.event_time}) after parent bias (et={winner.event_time})")


def _compute_sl(symbol: str, candidate: BiasCandidate) -> Optional[float]:
    minutes = _PARENT_MINUTES[candidate.parent]
    trails = read_trail_stops(symbol, minutes)
    if trails is None:
        return None
    return str_sl(symbol, candidate.direction, trails[0], trails[1])
