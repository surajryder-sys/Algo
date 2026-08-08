"""Entry price / SL calculation for the merged USOIL+BTCUSD+ETHUSD V2 bot.

Same entry mechanism for every symbol (M5 and M15 both use it -- M15
reuses M5's exact numbers within each symbol, same as USOIL always did):
market order if within MARKET_MAX of the zone edge, a shallow pullback
entry if between PULLBACK_MIN and PULLBACK_MAX, otherwise no trade.
Pullback entry is measured as a % giveback of however far price already
ran from the OB edge, floored at PULLBACK_MIN_EDGE_OFFSET so it never
demands an unreasonably small giveback just because that run was short
(ported from algo_v2/entries.py's XAUUSD fix; floor = PULLBACK_MIN for
every symbol here, same pattern used for USOIL and now BTC/ETH too).

These are absolute per-symbol price distances (not points) -- each
symbol's price scale is wildly different (USOIL ~$60-90, ETHUSD ~$1,900,
BTCUSD ~$65,000), so every constant below is symbol-specific, kept in one
EntryConfig per symbol rather than module-level constants (which was fine
back when this bot only ever traded USOIL).

USOIL: unchanged from the standalone algo_v2_usoil bot -- given directly
  by the user, not copied from XAUUSD.
BTCUSD / ETHUSD: pulled from the old (now-removed) btc_smc/eth_smc bots'
  final tuned values (commits ff4945d / 7713cf3) -- same PULLBACK_PCT
  (0.45) both bots already converged on independently, which also matches
  USOIL's. The PULLBACK_MIN_EDGE_OFFSET floor didn't exist in those old
  bots (it's a later fix); applied here to match, per explicit spec, at
  floor = PULLBACK_MIN same as USOIL/XAUUSD.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

PULLBACK_PCT = 0.45


@dataclass(frozen=True)
class EntryConfig:
    sl_buffer: float
    market_max: float
    pullback_min: float
    pullback_max: float
    pullback_min_edge_offset: float


ENTRY_CONFIGS = {
    "USOIL": EntryConfig(sl_buffer=0.100, market_max=0.600, pullback_min=0.600,
                         pullback_max=0.900, pullback_min_edge_offset=0.600),
    "BTCUSD": EntryConfig(sl_buffer=100.0, market_max=175.0, pullback_min=175.0,
                          pullback_max=800.0, pullback_min_edge_offset=175.0),
    "ETHUSD": EntryConfig(sl_buffer=3.0, market_max=4.0, pullback_min=4.0,
                          pullback_max=20.0, pullback_min_edge_offset=4.0),
}


class EntryMode(Enum):
    NONE = "NONE"
    MARKET = "MARKET"
    PENDING = "PENDING"


@dataclass(frozen=True)
class EntryPlan:
    mode: EntryMode
    entry_price: Optional[float]  # None when mode is MARKET (fill at send time) or NONE


def tiered_entry(symbol: str, direction: int, ob_edge: float, detected_price: float) -> EntryPlan:
    """direction: 1 bullish (ob_edge = ob.high), -1 bearish (ob_edge = ob.low).
    distance is always measured as how far price ran away from the zone
    edge. Used by both M5 and M15 candidates -- both tiers share one
    EntryConfig per symbol."""
    cfg = ENTRY_CONFIGS[symbol]

    if direction == 1:
        distance = detected_price - ob_edge
    else:
        distance = ob_edge - detected_price

    if distance < 0:
        return EntryPlan(EntryMode.NONE, None)

    if distance <= cfg.market_max:
        return EntryPlan(EntryMode.MARKET, None)

    if cfg.pullback_min < distance < cfg.pullback_max:
        # Offset from the OB edge shrinks naturally as distance shrinks
        # (raw offset = distance * (1 - PULLBACK_PCT)); floored at
        # pullback_min_edge_offset so short-distance setups don't end up
        # demanding an almost-full giveback just to reach an entry that's
        # already only a sliver off the edge.
        offset_from_edge = max(distance * (1 - PULLBACK_PCT), cfg.pullback_min_edge_offset)
        if direction == 1:
            entry = ob_edge + offset_from_edge
        else:
            entry = ob_edge - offset_from_edge
        return EntryPlan(EntryMode.PENDING, entry)

    return EntryPlan(EntryMode.NONE, None)


def select_sl(symbol: str, direction: int, entry_price: float, candidate_edges: dict) -> Optional[float]:
    """candidate_edges: {"M5": edge_or_None, "M15": edge_or_None}. Picks
    whichever edge is closest to entry_price, but only among edges on the
    geometrically valid side of entry -- below entry for a buy, above
    entry for a sell. An edge on the wrong side would produce a backwards
    SL (broker-rejected as invalid stops) and must never be chosen just
    for being numerically closest."""
    cfg = ENTRY_CONFIGS[symbol]

    valid_side = {
        tf: edge for tf, edge in candidate_edges.items()
        if edge is not None and ((direction == 1 and edge < entry_price) or
                                  (direction == -1 and edge > entry_price))
    }
    if not valid_side:
        return None

    closest_tf = min(valid_side, key=lambda tf: abs(valid_side[tf] - entry_price))
    edge = valid_side[closest_tf]
    return edge - cfg.sl_buffer if direction == 1 else edge + cfg.sl_buffer
