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
    comment_tag: str  # e.g. "V4S-M15-ICT-M5-STR" -- same V4S prefix as XAUUSD's own comments


@dataclass
class EvaluationResult:
    decision: Optional[Decision]
    reason: str


class EngineState:
    def __init__(self, path: str):
        self._path = Path(path)
        # symbol -> {"candidate_key": [...] or None, "fired": bool,
        #            "last_confirmation_event_time": int or None}
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
        """dict.setdefault only fills in the default when `symbol` is
        missing ENTIRELY -- confirmed live bug, 2026-08-30: this state file
        already had a "BTCUSD" entry from before last_confirmation_event_
        time existed (old schema: just candidate_key/fired), so setdefault
        silently returned that OLD, incomplete dict unchanged, and
        .get("last_confirmation_event_time") on it always came back None
        -- "never used before" -- for every check against a pre-existing
        symbol, completely defeating the confirmation-reuse fix for any
        state file that predated it. A stale 25-minute-old M5 confirmation
        got reused to fire a real trade because of exactly this gap.
        Backfilling missing keys onto the EXISTING dict (not just
        inserting a fresh one when the whole entry is absent) closes it."""
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
        """The event_time of whichever M5 confirmation last actually fired
        a trade for this symbol -- confirmed live bug, 2026-08-29: a real
        M5 flip correctly confirmed one parent candidate, then a SECOND,
        different candidate (an OB whose real formation time predated the
        first fire, but only became visible in the scraper's zone file
        afterward -- normal OB-detection lag, see parent_bias.py's own
        docstring) retroactively became "more recent" and reused that
        SAME already-consumed M5 event to fire again, even though nothing
        new had actually happened on the M5 chart -- "not a flip candle."
        A confirmation can only ever fire ONE trade, ever, regardless of
        how many different parent candidates it would technically satisfy
        the ordering check for."""
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
    """current_position_direction: this symbol's REAL current open position
    direction from MT5 ("buy"/"sell"/None) -- confirmed live bug,
    2026-08-29: without this, nothing stopped a second, independently-
    confirmed candidate from stacking a DUPLICATE same-direction position
    on top of an already-open one (this account is RETAIL_HEDGING, not
    netting -- MT5 will not merge/reject a same-direction order on its
    own). A same-direction winner while already positioned that way is
    blocked WITHOUT being marked fired -- it isn't resolved, just
    temporarily redundant, and should still be allowed to fire later if
    the existing position closes first and this candidate is still
    winning. An OPPOSITE-direction winner still fires normally here;
    main.py is responsible for closing the existing position before
    sending the new order (this module has no MT5 access)."""
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

    if confirm.event_time <= winner.event_time:
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
                                       f"SL inputs unavailable this poll (zone/trail data missing) -- waiting")

    state.mark_fired(symbol, confirm.event_time)
    decision = Decision(direction=winner.direction, sl=sl, candidate=winner, confirm=confirm,
                         comment_tag=f"V4S-{winner.tag}-M5-STR")
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
