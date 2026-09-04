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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from v4.crypto_trend_manager.m5_confirm import Confirmation, check_confirmation
from v4.crypto_trend_manager.parent_bias import BiasCandidate, winning_candidate
from v4.crypto_trend_manager.sl import str_sl
from v4.crypto_trend_manager.tv_reader import MAX_SCRAPER_AGE_SECONDS, is_scraper_alive, read_structure, read_trail_stops

Direction = Literal["buy", "sell"]

_PARENT_MINUTES = {"M30": 30, "M15": 15}

# Broker rejections that will NEVER succeed on an identical immediate
# retry. Deliberately narrow -- e.g. 10016 "Invalid stops" is NOT here:
# that one DOES resolve itself as price moves and needs to keep retrying
# every poll (same reasoning as usoil_ustec_trend_manager's own fix, see
# its engine.py/main.py). These two don't -- retrying them changes
# nothing until an external condition changes (margin freed, session
# reopens), so hammering the broker every poll_seconds is pure waste.
# Confirmed live 2026-09-02: this exact bug pattern, same day, generated
# ~20,000 failed order-send calls for a stuck USOIL signal in the twin
# module -- fixing here too since the code (and the bug) is identical.
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
    comment_tag: str  # e.g. "V4S-M15-STR-M5-STR" -- same V4S prefix as XAUUSD's own comments


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
        entry.setdefault("fatal_failure_event_time", None)
        entry.setdefault("fatal_failure_at", None)
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
    # Strictly-less-than only -- confirm.event_time == winner.event_time
    # (parent and M5 both flipping in the exact same instant) is explicitly
    # VALID per the user's own example, 2026-08-30 ("a bullish flip
    # happened in M15 at 9:15, same time lowertimeframe also confirmed
    # exactly at same time... both conditions meeting exactly at same time
    # also works"). A strict > here would wrongly reject that simultaneous
    # case as "stale."

    last_used = state.last_confirmation_event_time(symbol)
    if last_used is not None and confirm.event_time <= last_used:
        return EvaluationResult(None, f"{winner.tag} {winner.direction} winning but its M5 {confirm.kind} "
                                       f"confirmation (et={confirm.event_time}) already fired an earlier trade "
                                       f"(last used et={last_used}) -- not a fresh flip, waiting for a new one")

    sl, sl_reject_reason = _compute_sl(symbol, winner, current_price)
    if sl is None:
        return EvaluationResult(None, f"{winner.tag} {winner.direction} confirmed by M5 ({confirm.kind}) but "
                                       f"{sl_reject_reason} -- waiting")

    # NOT marked fired here anymore -- ported 2026-08-31 from the same fix
    # confirmed live in usoil_ustec_trend_manager's own engine.py: this
    # used to commit "fired" the instant a decision was built, before the
    # order was even attempted, so a broker-side rejection still burned
    # the confirmation and left the account flat with no retry. The
    # caller (main.py's own fire function) now only calls
    # state.mark_fired() after confirming the order actually went
    # through -- see that module's own docstring/comment for the full
    # incident (found on USOIL, same shared engine shape as here).
    decision = Decision(direction=winner.direction, sl=sl, candidate=winner, confirm=confirm,
                         comment_tag=f"V4S-{winner.tag}-M5-STR")
    return EvaluationResult(decision, f"FIRING {winner.direction} via {winner.tag} -- M5 {confirm.kind} "
                                       f"confirmation (et={confirm.event_time}) after parent bias (et={winner.event_time})")


def _compute_sl(symbol: str, candidate: BiasCandidate,
                 current_price: Optional[float]) -> tuple[Optional[float], Optional[str]]:
    """(sl, reject_reason) -- reject_reason is only ever non-None alongside
    sl=None, and always explains exactly why. Ported 2026-08-31 from the
    same fix confirmed live in usoil_ustec_trend_manager's own engine.py:
    str_sl() computes SL from the parent timeframe's OWN live trail lines
    at the exact instant of firing, not from a stable snapshot taken when
    the candidate first formed -- if the parent's lines have flickered
    back toward the opposite direction in the gap between candidate
    formation and M5 confirmation, the computed SL can land on the wrong
    side of live price entirely (a real USOIL incident: SL above price
    for a buy), which MT5 rejects outright. Now explicitly checked
    against the real live price before ever returning a decision."""
    minutes = _PARENT_MINUTES[candidate.parent]
    trails = read_trail_stops(symbol, minutes)
    if trails is None:
        return None, "SL inputs unavailable this poll (zone/trail data missing)"
    sl = str_sl(symbol, candidate.direction, trails[0], trails[1])
    if sl is None:
        return None, "SL inputs unavailable this poll (zone/trail data missing)"
    if current_price is not None:
        wrong_side = (sl >= current_price) if candidate.direction == "buy" else (sl <= current_price)
        if wrong_side:
            return None, (f"computed SL {sl:.3f} is on the wrong side of live price {current_price:.3f} -- "
                           f"{candidate.parent}'s own trail lines have moved since the {candidate.direction} "
                           f"candidate formed (et={candidate.event_time})")
    return sl, None
