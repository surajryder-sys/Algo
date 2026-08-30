"""Resolves the single winning parent-bias candidate for a symbol, across
BOTH parent timeframes (M30, M15) -- whichever has the single most recent
event_time wins ("whichever parent confirms first, will win the bias").

STR (structure) candidates include PARTIAL parent-level moves, not just
full STRONG/WEAK (added 2026-08-30, user's explicit worked example: M15
going STRONG -> INDECISIVE via a bearish flip is itself a real "partial
bearish" bias event, racing on equal footing with a full flip -- not
something to ignore until the parent fully completes to WEAK). Reuses
m5_confirm.check_confirmation() directly rather than reimplementing the
full/partial state machine a second time -- that function already takes
any StructureReading and a direction and answers exactly this question;
it isn't M5-specific despite the module's name. M5's OWN confirmation
requirement doesn't change based on whether the parent's event was full or
partial -- per the user's own examples, M5 needs "at least partial" either
way -- so engine.py needs no changes for this, only candidate generation
here does.

ICT (OB-zone-based) candidates were removed entirely 2026-08-30 (explicit
request, "remove ict based trade completely") -- this module, sl.py, and
tv_reader.py no longer read or reason about OB zones at all; entries are
purely structure-based now. (Prior to removal, an ICT candidate could win
the recency race using a zone whose formation timestamp Pine hadn't
confirmed yet, misdating an ancient, ~8,700-point-away zone as the
freshest event on the chart and firing a real trade off it -- see the
git history for the full incident. Removing ICT sidesteps that whole
class of risk rather than only patching the one manifestation of it.)

A candidate with no clear direction (structure is UNDECISIVE with no
directional flip at all yet) is simply not produced -- there is nothing to
compare it against, not a "None direction" entry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from v4.crypto_trend_manager.m5_confirm import check_confirmation
from v4.crypto_trend_manager.tv_reader import read_structure

Direction = Literal["buy", "sell"]
ParentTF = Literal["M30", "M15"]

_PARENTS: tuple[tuple[ParentTF, int], ...] = (("M30", 30), ("M15", 15))


@dataclass(frozen=True)
class BiasCandidate:
    parent: ParentTF
    direction: Direction
    event_time: int

    @property
    def key(self) -> tuple:
        """Identity for "is this the SAME candidate as before" comparisons
        in engine.py -- deliberately excludes nothing; two candidates are
        the same setup iff every field matches."""
        return (self.parent, self.direction, self.event_time)

    @property
    def tag(self) -> str:
        return f"{self.parent}-STR"


def candidates_for_symbol(symbol: str) -> list[BiasCandidate]:
    out: list[BiasCandidate] = []
    for parent, minutes in _PARENTS:
        structure = read_structure(symbol, minutes)
        if structure is None:
            continue
        # Full (STRONG/WEAK) or partial (UNDECISIVE via a directional
        # flip) -- check_confirmation's own full/partial rule applies
        # identically to a parent's structure as it does to M5's (see
        # this module's own docstring). buy_confirm/sell_confirm are
        # mutually exclusive by construction -- a structure reading is
        # never simultaneously "confirmed" bullish and bearish.
        buy_confirm = check_confirmation(structure, "buy")
        sell_confirm = check_confirmation(structure, "sell")
        if buy_confirm is not None:
            out.append(BiasCandidate(parent, "buy", buy_confirm.event_time))
        elif sell_confirm is not None:
            out.append(BiasCandidate(parent, "sell", sell_confirm.event_time))
        # Neither -- structure has never shown a directional flip on this
        # timeframe at all yet -- produces no candidate.
    return out


def winning_candidate(symbol: str) -> Optional[BiasCandidate]:
    candidates = candidates_for_symbol(symbol)
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.event_time)
