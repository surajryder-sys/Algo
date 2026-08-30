"""Initial SL calculation -- the parent timeframe's own FAR ATR trail line
(whichever of line1/line2 sits further from current price), with a
buffer. Structure-only entries, same shape as crypto_trend_manager's own
sl.py post-ICT-removal.

SL_BUFFER values are the exact ones already proven for these two symbols
by the old (now-stopped) v3 signal_engine -- see
v3/signal_engine/entries.py's own SYMBOL_SL_BUFFER dict. Copied here as
this bot's own constant rather than importing that module, per this
repo's usual per-bot-isolation convention.
"""
from __future__ import annotations

from typing import Literal, Optional

Direction = Literal["buy", "sell"]

SL_BUFFER = {"USOIL": 0.100, "USTEC": 20.0}


def str_sl(symbol: str, direction: Direction, line1_trail: Optional[float],
           line2_trail: Optional[float]) -> Optional[float]:
    """None if neither line's trail_stop is available this poll."""
    candidates = [v for v in (line1_trail, line2_trail) if v is not None]
    if not candidates:
        return None
    far = min(candidates) if direction == "buy" else max(candidates)
    buffer = SL_BUFFER[symbol]
    return far - buffer if direction == "buy" else far + buffer
