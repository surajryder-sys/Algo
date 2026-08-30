"""Resolves the single winning parent-bias candidate for a symbol, across
BOTH parent timeframes (M30, M15) -- whichever has the single most recent
event_time wins. Identical logic to crypto_trend_manager's own
parent_bias.py (post-ICT-removal shape) -- structure-only, full or
partial, reusing m5_confirm.check_confirmation() directly. Own copy per
this repo's usual per-bot isolation convention.

H1 is also on the shared chart/scraper grid (see tv_reader.py) but is
NOT a parent here -- explicit user scope, 2026-08-31: only M30 and M15
are parent bias, M5 is execution. H1 is read by the scraper but unused
by this engine, same "present but reserved" status M1 has for
crypto_trend_manager's own future Reversal Manager.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from v4.usoil_ustec_trend_manager.m5_confirm import check_confirmation
from v4.usoil_ustec_trend_manager.tv_reader import read_structure

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
        buy_confirm = check_confirmation(structure, "buy")
        sell_confirm = check_confirmation(structure, "sell")
        if buy_confirm is not None:
            out.append(BiasCandidate(parent, "buy", buy_confirm.event_time))
        elif sell_confirm is not None:
            out.append(BiasCandidate(parent, "sell", sell_confirm.event_time))
    return out


def winning_candidate(symbol: str) -> Optional[BiasCandidate]:
    candidates = candidates_for_symbol(symbol)
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.event_time)
