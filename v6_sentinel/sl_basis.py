"""Shared initial-SL basis for RM-STR and RM-ICT: prefer the FARTHEST
usable ATR-dual / Supertrend line on M5, then on M3, and only fall back
to CISD's own swing high/low when neither timeframe has a usable line.
Extracted 2026-09-19 out of reversal_entry.py (where it was first built
for RM-STR) once RM-ICT needed the identical rule for its own SL-distance
case, so the two components can't drift apart.

USABLE (confirmed with the user): a line is usable only if it sits
strictly on the correct side of the live entry price -- ABOVE it for a
SELL (direction -1), BELOW it for a BUY (direction 1). A line on the
wrong side (e.g. every line below price as support during a bearish-CISD
SELL) can't be a stop. Any single line is enough. If several are usable
on one timeframe, the FARTHEST from entry wins (widest SL -- the user's
explicit choice over nearest): the highest usable line for a SELL, the
lowest for a BUY. There is deliberately NO maximum-distance cap here.

M5 is tried first and M3 only if M5 has no usable line. Lines are
computed straight from copy_rates (rates.read_trail_series /
rates.read_supertrend -- no chart or indicator needed) and cached in a
caller-supplied dict so several signals in one cycle don't recompute
them.

The returned basis is WITHOUT buffer -- the caller applies it (minus for
a BUY, plus for a SELL).

OPTIONAL "MUST REDUCE RISK" CHECK (RM-ICT, confirmed with the user
2026-09-19: "if the new sl is 22 points, dont take new sl of 22 points,
then apply same sl... it was to reduce risk we were searching for lines
on M5 and m3, not to increase risk or to wide sl"): when the caller
passes must_beat_sl (the SL it would use otherwise) plus sl_buffer, the
candidate is picked by the normal rule above (farthest usable line on
M5, else M3, else swing) and THEN compared: if its final SL (basis
-/+ buffer) is not strictly tighter than must_beat_sl -- higher for a
BUY, lower for a SELL -- it is REJECTED and None is returned, meaning
"keep the original SL". It is deliberately a reject, not a re-search:
no hunting for a nearer line on the same timeframe, and no falling
through to M3 or swing after a rejection. Without must_beat_sl (RM-STR,
which has no prior SL to beat) nothing is checked and behaviour is
exactly as before.
"""
from __future__ import annotations

from typing import Optional

from v6_sentinel import cisd_bridge, rates

SL_LINE_TIMEFRAMES = (5, 3)  # preference order: M5 first, M3 only if M5 has none usable


def line_values(symbol: str, tf_minutes: int, cache: dict) -> list[tuple[str, float]]:
    """(label, value) for every line currently available on one
    timeframe -- ATR dual line1/line2 and the native Supertrend, each
    independently (a missing source just contributes nothing)."""
    if tf_minutes in cache:
        return cache[tf_minutes]
    values: list[tuple[str, float]] = []
    series = rates.read_trail_series(symbol, tf_minutes)
    if series is not None:
        for label, v in (("ATR1", series.trail1[-1]), ("ATR2", series.trail2[-1])):
            if v is not None:
                values.append((label, v))
    st = rates.read_supertrend(symbol, tf_minutes)
    if st is not None:
        values.append(("ST", st.supertrend))
    cache[tf_minutes] = values
    return values


def _pick(symbol: str, direction: int, entry_price: float, cisd, cache: dict) -> Optional[tuple[float, str]]:
    for tf_minutes in SL_LINE_TIMEFRAMES:
        usable = [(label, v) for label, v in line_values(symbol, tf_minutes, cache)
                  if (v > entry_price if direction == -1 else v < entry_price)]
        if usable:
            label, v = max(usable, key=lambda x: x[1]) if direction == -1 else min(usable, key=lambda x: x[1])
            return v, f"M{tf_minutes}/{label}"
    swing = cisd_bridge.sl_basis(cisd)
    return (swing, "SWING") if swing is not None else None


def initial_sl_basis(symbol: str, direction: int, entry_price: float, cisd, cache: dict,
                     sl_buffer: float = 0.0, must_beat_sl: Optional[float] = None) -> Optional[tuple[float, str]]:
    """(basis price WITHOUT buffer, source label) or None if nothing is
    usable. Source label is "M5/ATR2", "M3/ST", ... for a line, or
    "SWING" for the CISD fallback (cisd_bridge.sl_basis(), the nearest
    active swing high for a SELL / low for a BUY, frozen at the bar CISD
    confirmed -- None if no active swing existed then). If must_beat_sl is
    given and the picked candidate's final SL (basis -/+ sl_buffer) is not
    strictly tighter than it, returns None -- see module docstring."""
    resolved = _pick(symbol, direction, entry_price, cisd, cache)
    if resolved is None or must_beat_sl is None:
        return resolved
    basis = resolved[0]
    final = basis - sl_buffer if direction == 1 else basis + sl_buffer
    tighter = final > must_beat_sl if direction == 1 else final < must_beat_sl
    return resolved if tighter else None
