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
import time
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

# Broker rejections that will NEVER succeed on an identical immediate
# retry. Deliberately narrow -- e.g. 10016 "Invalid stops" is NOT here:
# the 2026-08-31 fix (see main.py) exists specifically because that one
# DOES resolve itself as price moves and needs to keep retrying every
# poll. These two don't -- retrying them changes nothing until an
# external condition changes (margin freed, session reopens), so
# hammering the broker every poll_seconds is pure waste. Confirmed live
# 2026-09-02: a single stuck USOIL signal generated ~20,000 failed
# order-send calls in one day, [No money] every time.
FATAL_RETCODES = {
    10019,  # TRADE_RETCODE_NO_MONEY -- insufficient free margin
    10018,  # TRADE_RETCODE_MARKET_CLOSED -- won't open until session resumes
}


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
        entry.setdefault("fatal_failure_event_time", None)
        entry.setdefault("fatal_failure_at", None)
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

    def fatal_failure_active(self, symbol: str, confirmation_event_time: int, cooldown_seconds: float) -> bool:
        """True if THIS EXACT confirmation already failed with a
        non-retryable broker rejection (e.g. no money) within the last
        cooldown_seconds -- caller should skip re-attempting the order
        entirely rather than hammering the terminal every poll. A
        different confirmation_event_time (a genuinely new signal) is
        never suppressed -- only a repeat of the same doomed one."""
        entry = self._entry(symbol)
        if entry["fatal_failure_event_time"] != confirmation_event_time:
            return False
        failed_at = entry["fatal_failure_at"]
        if failed_at is None:
            return False
        return (time.time() - failed_at) < cooldown_seconds

    def record_fatal_failure(self, symbol: str, confirmation_event_time: int) -> None:
        entry = self._entry(symbol)
        entry["fatal_failure_event_time"] = confirmation_event_time
        entry["fatal_failure_at"] = time.time()
        self._save()


def evaluate_symbol(state: EngineState, symbol: str, current_position_direction: Optional[str] = None,
                     current_price: Optional[float] = None) -> EvaluationResult:
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

    sl, sl_reject_reason = _compute_sl(symbol, winner, current_price)
    if sl is None:
        return EvaluationResult(None, f"{winner.tag} {winner.direction} confirmed by M5 ({confirm.kind}) but "
                                       f"{sl_reject_reason} -- waiting")

    # NOT marked fired here anymore -- fixed 2026-08-31, confirmed live:
    # this used to commit "fired" the instant a decision was built, before
    # the order was even attempted. A real USOIL buy then failed at the
    # broker (retcode 10016 "Invalid stops") and the state was already
    # burned -- the account sat flat with the engine believing this M5
    # confirmation had been used, permanently skipping any retry. Now the
    # caller (main.py's run_once) only calls state.mark_fired() after
    # confirming the order actually went through -- see its own
    # docstring/comment for the full incident.
    decision = Decision(direction=winner.direction, sl=sl, candidate=winner, confirm=confirm,
                         comment_tag=f"V4S-{winner.tag}-M5-STR")
    return EvaluationResult(decision, f"FIRING {winner.direction} via {winner.tag} -- M5 {confirm.kind} "
                                       f"confirmation (et={confirm.event_time}) after parent bias (et={winner.event_time})")


def _compute_sl(symbol: str, candidate: BiasCandidate,
                 current_price: Optional[float]) -> tuple[Optional[float], Optional[str]]:
    """(sl, reject_reason) -- reject_reason is only ever non-None alongside
    sl=None, and always explains exactly why. Extended 2026-08-31,
    confirmed live: str_sl() computes SL from the parent timeframe's OWN
    live trail lines at the exact instant of firing, not from a stable
    snapshot taken when the candidate first formed. A real USOIL buy
    candidate (M15 partial-bullish at 19:15 IST) wasn't actually fired
    until M5 confirmed 10 minutes later at 19:25 -- by then M15's own
    lines had flickered back toward bearish, so str_sl's usual "far line
    minus a small buffer" landed ABOVE the live price (85.500 vs a live
    ask of 85.379), which is nonsensical for a buy SL and got rejected
    outright by MT5. Now explicitly checked against the real live price
    before ever returning a decision -- if the computed SL is on the
    wrong side, this is treated as "the parent's own structure has moved
    since the candidate formed" and the engine waits for a poll where
    M15's lines are back in a state that produces a genuinely valid SL,
    rather than sending a broken order and burning the confirmation."""
    minutes = _PARENT_MINUTES[candidate.parent]
    trails = read_trail_stops(symbol, minutes)
    if trails is None:
        return None, "SL inputs unavailable this poll (trail data missing)"
    sl = str_sl(symbol, candidate.direction, trails[0], trails[1])
    if sl is None:
        return None, "SL inputs unavailable this poll (trail data missing)"
    if current_price is not None:
        wrong_side = (sl >= current_price) if candidate.direction == "buy" else (sl <= current_price)
        if wrong_side:
            return None, (f"computed SL {sl:.3f} is on the wrong side of live price {current_price:.3f} -- "
                           f"{candidate.parent}'s own trail lines have moved since the {candidate.direction} "
                           f"candidate formed (et={candidate.event_time})")
    return sl, None
