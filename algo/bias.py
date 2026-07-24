"""SMC bias state machine: combines M15/M5/M3 order-block direction into a
Strong/Medium/ShortTerm call, per the rules given for the XAUUSD EA.

Core rule: whichever of M15/M5/M3 has the most recently formed OB (by origin
time, not detection time) is the "trigger" for the current direction call.
The other two timeframes either confirm or disagree with the trigger's
direction, and that agreement count sets the state:

  both agree            -> STRONG
  one agrees, one doesn't -> MEDIUM
  neither agrees (trigger is the lone signal) -> SHORTTERM, sourced from
      whichever single timeframe (M3 or M5) is the trigger

ShortTerm can coexist with an opposite-direction position; Strong blocks/
closes the opposite direction entirely. Medium allows the agreeing
timeframes (plus M1) to trade; the disagreeing timeframe does not.
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
    trigger_tf: str              # "M15" / "M5" / "M3" / "" if NONE
    agreeing_tfs: tuple          # the other timeframe(s) that agree with trigger
    disagreeing_tfs: tuple       # the other timeframe(s) that don't
    shortterm_source_tf: Optional[str] = None  # set only for SHORTTERM states


def compute_bias(m15: TFBias, m5: TFBias, m3: TFBias) -> BiasResult:
    tfs = {"M15": m15, "M5": m5, "M3": m3}
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
    """Which of {"M1","M3","M5"} may attempt an entry for the current bias
    direction. M15 never executes trades itself (bias-only)."""
    if bias.state in (BiasState.BULLISH_STRONG, BiasState.BEARISH_STRONG):
        return frozenset({"M1", "M3", "M5"})

    if bias.state in (BiasState.BULLISH_MEDIUM, BiasState.BEARISH_MEDIUM):
        allowed = {"M1"}
        for tf in ("M3", "M5"):
            if tf == bias.trigger_tf or tf in bias.agreeing_tfs:
                allowed.add(tf)
        return frozenset(allowed)

    if bias.state in (BiasState.BULLISH_SHORTTERM, BiasState.BEARISH_SHORTTERM):
        return frozenset({"M1", bias.shortterm_source_tf})

    return frozenset()
