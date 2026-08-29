"""Initial SL calculation -- depends on which kind of setup actually fired
(see parent_bias.BiasCandidate.kind):
  - ICT (OB-initiated): the OB zone's own OPPOSITE edge from entry, with a
    buffer -- buy: zone low minus buffer, sell: zone high plus buffer.
  - STR (structure-initiated): the parent timeframe's own FAR ATR trail
    line (whichever of line1/line2 sits further from current price, same
    "far line = safer/wider stop" concept v4/trend_manager/m1_execution.py
    already uses for XAUUSD's M1), with the same buffer.

SL_BUFFER values are the exact ones already proven for these two symbols
by the old (now-stopped) v3 crypto Trend Manager -- see
v3/signal_engine/entries.py's own SYMBOL_SL_BUFFER dict and
initial_sl_from_parent's docstring, which used this SAME parent-OB-buffer
concept for "M15/M30 for BTC/ETH" already. Copied here as this bot's own
constant rather than importing that module, per this repo's usual
per-bot-isolation convention -- not because the values themselves need
retuning.
"""
from __future__ import annotations

from typing import Literal, Optional

Direction = Literal["buy", "sell"]

SL_BUFFER = {"BTCUSD": 20.0, "ETHUSD": 2.0}


def ict_sl(symbol: str, direction: Direction, zone_top: float, zone_btm: float) -> float:
    buffer = SL_BUFFER[symbol]
    return (zone_btm - buffer) if direction == "buy" else (zone_top + buffer)


def str_sl(symbol: str, direction: Direction, line1_trail: Optional[float],
           line2_trail: Optional[float]) -> Optional[float]:
    """None if neither line's trail_stop is available this poll (transient
    live-snapshot gap) -- callers should treat that the same as "can't
    confirm SL yet," not fire without one."""
    candidates = [v for v in (line1_trail, line2_trail) if v is not None]
    if not candidates:
        return None
    # "Far" = further from where price would need protecting, i.e. the
    # lower one for a buy (deeper stop) / the higher one for a sell.
    far = min(candidates) if direction == "buy" else max(candidates)
    buffer = SL_BUFFER[symbol]
    return far - buffer if direction == "buy" else far + buffer
