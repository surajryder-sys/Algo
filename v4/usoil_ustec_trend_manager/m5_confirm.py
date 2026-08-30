"""M5's confirmation state machine -- identical logic and rationale to
crypto_trend_manager's own m5_confirm.py (same user-specified rules apply
to USOIL/USTEC too, per 2026-08-31's "same as before" instruction). Own
copy rather than a cross-package import, per this repo's usual per-bot
isolation convention.

  - FULL confirmation for a direction = M5's combined structure is
    currently STRONG (buy) / WEAK (sell).
  - PARTIAL confirmation for a direction = M5's combined structure is
    currently UNDECISIVE, AND the specific line that most recently
    flipped to produce that UNDECISIVE state flipped IN that direction.
  - NOT a confirmation = UNDECISIVE reached by a flip in the OPPOSITE
    direction (a pullback FROM strength, not a move toward weakness).

See crypto_trend_manager/m5_confirm.py's own docstring for the full
worked examples this logic was built and verified against.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from v4.usoil_ustec_trend_manager.tv_reader import LineReading, StructureReading

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
