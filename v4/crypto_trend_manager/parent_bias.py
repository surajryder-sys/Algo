"""Resolves the single winning parent-bias candidate for a symbol, across
BOTH parent timeframes (M30, M15) and BOTH signal types (STR = that
parent's own ATR structure, ICT = that parent's own most recently formed
OB zone) -- whichever candidate has the single most recent event_time wins
("whichever parent confirms first, will win the bias" -- recency spans
timeframe AND signal type together, not just comparing M30 to M15 on one
signal alone).

STR candidates now include PARTIAL parent-level moves, not just full
STRONG/WEAK (added 2026-08-30, user's explicit worked example: M15 going
STRONG -> INDECISIVE via a bearish flip is itself a real "partial bearish"
bias event, racing on equal footing with everything else -- not something
to ignore until the parent fully completes to WEAK). Reuses
m5_confirm.check_confirmation() directly rather than reimplementing the
full/partial state machine a second time -- that function already takes
any StructureReading and a direction and answers exactly this question;
it isn't M5-specific despite the module's name. M5's OWN confirmation
requirement doesn't change based on whether the parent's event was full or
partial -- per the user's own examples, M5 needs "at least partial" either
way -- so engine.py needs no changes for this, only candidate generation
here does.

A candidate with no clear direction (STR: structure is UNDECISIVE with no
directional flip at all yet; ICT: no zone exists yet on that timeframe) is
simply not produced -- there is nothing to compare it against, not a "None
direction" entry.

Re-derived fully fresh on every call, no caching -- an ICT candidate whose
underlying zone gets mitigated between polls just stops appearing (see
tv_reader.read_latest_ob's own docstring), which is exactly the "invalid
OB gets pierced, parent bias reverts to whatever structure already
showed" behavior from the user's own worked trap example. engine.py is
responsible for noticing when the winning candidate's IDENTITY changes
between polls (a new candidate superseding a still-unconfirmed one).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from v4.crypto_trend_manager.m5_confirm import check_confirmation
from v4.crypto_trend_manager.tv_reader import read_latest_ob, read_structure

Direction = Literal["buy", "sell"]
ParentTF = Literal["M30", "M15"]
Kind = Literal["STR", "ICT"]

_PARENTS: tuple[tuple[ParentTF, int], ...] = (("M30", 30), ("M15", 15))


@dataclass(frozen=True)
class BiasCandidate:
    parent: ParentTF
    kind: Kind
    direction: Direction
    event_time: int

    @property
    def key(self) -> tuple:
        """Identity for "is this the SAME candidate as before" comparisons
        in engine.py -- deliberately excludes nothing; two candidates are
        the same setup iff every field matches."""
        return (self.parent, self.kind, self.direction, self.event_time)

    @property
    def tag(self) -> str:
        return f"{self.parent}-{self.kind}"


def candidates_for_symbol(symbol: str) -> list[BiasCandidate]:
    out: list[BiasCandidate] = []
    for parent, minutes in _PARENTS:
        structure = read_structure(symbol, minutes)
        if structure is not None:
            # Full (STRONG/WEAK) or partial (UNDECISIVE via a directional
            # flip) -- check_confirmation's own full/partial rule applies
            # identically to a parent's structure as it does to M5's (see
            # this module's own docstring). buy_confirm/sell_confirm are
            # mutually exclusive by construction -- a structure reading is
            # never simultaneously "confirmed" bullish and bearish.
            buy_confirm = check_confirmation(structure, "buy")
            sell_confirm = check_confirmation(structure, "sell")
            if buy_confirm is not None:
                out.append(BiasCandidate(parent, "STR", "buy", buy_confirm.event_time))
            elif sell_confirm is not None:
                out.append(BiasCandidate(parent, "STR", "sell", sell_confirm.event_time))
            # Neither -- structure has never shown a directional flip on
            # this timeframe at all yet -- produces no STR candidate.

        zone = read_latest_ob(symbol, minutes)
        if zone is not None:
            out.append(BiasCandidate(parent, "ICT", zone.direction, zone.start_time))
    return out


def winning_candidate(symbol: str) -> Optional[BiasCandidate]:
    candidates = candidates_for_symbol(symbol)
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.event_time)
