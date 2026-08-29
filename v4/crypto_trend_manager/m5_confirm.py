"""M5's confirmation state machine -- the ONLY thing that actually fires a
trade, for both STR- and ICT-initiated setups alike ("both will need to
have the confirmations from lowertime frame, without confirmation no
trades"). Per the user's explicit worked examples, 2026-08-29:

  - FULL confirmation for a direction = M5's combined structure is
    currently STRONG (buy) / WEAK (sell) -- fires immediately, however
    that state was reached (a single candle crossing both lines at once,
    or completing from INDECISIVE).
  - PARTIAL confirmation for a direction = M5's combined structure is
    currently UNDECISIVE, AND the specific line that most recently
    flipped to produce that UNDECISIVE state flipped IN that direction
    (WEAK -> UNDECISIVE via a bullish flip = valid partial buy
    confirmation). This still fires -- "still considerable to enter".
  - NOT a confirmation for that direction = UNDECISIVE reached by a flip
    in the OPPOSITE direction (STRONG -> UNDECISIVE via a bearish flip is
    a pullback FROM strength, not a move toward weakness) -- worked
    example: this only becomes valid once either (a) the same line flips
    back, completing STRONG again (full confirmation), or (b) the
    remaining line also flips, completing WEAK, and only THEN a fresh
    bullish flip from that confirmed WEAK state counts as a new, valid
    partial buy confirmation. Nothing here needs to special-case that
    sequence explicitly -- it falls out naturally from just asking "which
    line moved LAST, and which way" on every poll.

The confirmation's own event_time is the deciding line's own event_time
(partial) or the combined structure's own event_time (full) -- callers
(engine.py) additionally require this to be AFTER the parent bias
candidate's event_time, so a stale M5 reading from before the parent bias
even existed can't be reused to confirm it (explicit rule, 2026-08-29).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from v4.crypto_trend_manager.tv_reader import LineReading, StructureReading

Direction = Literal["buy", "sell"]
ConfirmKind = Literal["full", "partial"]


@dataclass
class Confirmation:
    kind: ConfirmKind
    event_time: int


def _most_recent_line(l1: LineReading, l2: LineReading) -> Optional[LineReading]:
    candidates = [l for l in (l1, l2) if l.event_time is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda l: l.event_time)


def check_confirmation(structure: StructureReading, direction: Direction) -> Optional[Confirmation]:
    full_state = "STRONG" if direction == "buy" else "WEAK"
    want_trend = 1 if direction == "buy" else -1

    if structure.state == full_state and structure.event_time is not None:
        return Confirmation("full", structure.event_time)

    if structure.state == "UNDECISIVE":
        last = _most_recent_line(structure.line1, structure.line2)
        if last is not None and last.trend == want_trend:
            return Confirmation("partial", last.event_time)

    return None
