"""SMC bias state machine for the ETHUSD bot: combines M5/M15/M30 order-block
direction into a Strong/Medium/ShortTerm call.

Core rule: whichever of M5/M15/M30 has the most recently formed OB (by origin
time, not detection time) is the "trigger" for the current direction call.
The other two timeframes either confirm or disagree with the trigger's
direction, and that agreement count sets the state:

  both agree            -> STRONG
  one agrees, one doesn't -> MEDIUM
  neither agrees (trigger is the lone signal) -> SHORTTERM, sourced from
      whichever single timeframe is the trigger

Unlike the XAUUSD bot, there's no bias-only non-trading timeframe here --
all three of M5/M15/M30 are peers that both feed the bias call and can fire
their own entries. Entry eligibility per state:

  STRONG    -> M5, M15, and M30 can all fire entries
  MEDIUM    -> only the trigger tf + the one agreeing tf can fire
  SHORTTERM -> only the trigger tf itself can fire
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class BiasState(Enum):
    NONE = "NONE"
    BULLISH_STRONG = "BULLISH_STRONG"
    BEARISH_STRONG = "BEARISH_STRONG"
    BULLISH_MEDIUM = "BULLISH_MEDIUM"
    BEARISH_MEDIUM = "BEARISH_MEDIUM"
    BULLISH_SHORTTERM = "BULLISH_SHORTTERM"
    BEARISH_SHORTTERM = "BEARISH_SHORTTERM"


@dataclass(frozen=True)
class TFBias:
    direction: int    # 1 bullish, -1 bearish, 0 = no OB yet
    origin_time: int  # OB origin (start_time), 0 if no OB yet


@dataclass(frozen=True)
class BiasResult:
    state: BiasState
    direction: int              # 1, -1, or 0
    trigger_tf: str              # "M5" / "M15" / "M30" / "" if NONE
    agreeing_tfs: tuple          # the other timeframe(s) that agree with trigger
    disagreeing_tfs: tuple       # the other timeframe(s) that don't
    shortterm_source_tf: Optional[str] = None  # set only for SHORTTERM states


def compute_bias(m5: TFBias, m15: TFBias, m30: TFBias) -> BiasResult:
    tfs = {"M5": m5, "M15": m15, "M30": m30}
    origins = {tf: b.origin_time for tf, b in tfs.items()}

    trigger_tf = max(origins, key=lambda tf: origins[tf])
    if origins[trigger_tf] == 0:
        return BiasResult(BiasState.NONE, 0, "", (), ())

    trigger_dir = tfs[trigger_tf].direction
    if trigger_dir == 0:
        return BiasResult(BiasState.NONE, 0, trigger_tf, (), ())

    others = [tf for tf in tfs if tf != trigger_tf]
    agreeing = tuple(tf for tf in others if tfs[tf].direction == trigger_dir)
    disagreeing = tuple(tf for tf in others if tfs[tf].direction != trigger_dir)

    if len(agreeing) == 2:
        state = BiasState.BULLISH_STRONG if trigger_dir == 1 else BiasState.BEARISH_STRONG
        return BiasResult(state, trigger_dir, trigger_tf, agreeing, disagreeing)

    if len(agreeing) == 1:
        state = BiasState.BULLISH_MEDIUM if trigger_dir == 1 else BiasState.BEARISH_MEDIUM
        return BiasResult(state, trigger_dir, trigger_tf, agreeing, disagreeing)

    # Neither other timeframe agrees: trigger is the lone signal.
    state = BiasState.BULLISH_SHORTTERM if trigger_dir == 1 else BiasState.BEARISH_SHORTTERM
    return BiasResult(state, trigger_dir, trigger_tf, agreeing, disagreeing,
                       shortterm_source_tf=trigger_tf)


def allowed_entry_sources(bias: BiasResult) -> frozenset:
    """Which of {"M5","M15","M30"} may attempt an entry for the current bias
    direction. No always-on fallback timeframe -- unlike the XAUUSD bot's M1,
    every source here is gated purely by the bias state."""
    if bias.state in (BiasState.BULLISH_STRONG, BiasState.BEARISH_STRONG):
        return frozenset({"M5", "M15", "M30"})

    if bias.state in (BiasState.BULLISH_MEDIUM, BiasState.BEARISH_MEDIUM):
        return frozenset({bias.trigger_tf, *bias.agreeing_tfs})

    if bias.state in (BiasState.BULLISH_SHORTTERM, BiasState.BEARISH_SHORTTERM):
        return frozenset({bias.shortterm_source_tf})

    return frozenset()
