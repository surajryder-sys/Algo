"""Resolves the single winning parent-bias candidate for a symbol, across
BOTH parent timeframes (M30, M15) and BOTH signal types (STR = that
parent's own ATR structure, ICT = that parent's own most recently formed
OB zone) -- four possible candidates total, whichever has the single most
recent event_time wins ("whichever parent confirms first, will win the
bias" -- recency spans timeframe AND signal type together, not just
comparing M30 to M15 on one signal alone).

A candidate with no clear direction (STR: structure is UNDECISIVE; ICT: no
zone exists yet on that timeframe) is simply not produced -- there is
nothing to compare it against, not a "None direction" entry.

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
        if structure is not None and structure.event_time is not None:
            if structure.state == "STRONG":
                out.append(BiasCandidate(parent, "STR", "buy", structure.event_time))
            elif structure.state == "WEAK":
                out.append(BiasCandidate(parent, "STR", "sell", structure.event_time))
            # UNDECISIVE parent structure produces no STR candidate at all.

        zone = read_latest_ob(symbol, minutes)
        if zone is not None:
            out.append(BiasCandidate(parent, "ICT", zone.direction, zone.start_time))
    return out


def winning_candidate(symbol: str) -> Optional[BiasCandidate]:
    candidates = candidates_for_symbol(symbol)
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.event_time)
