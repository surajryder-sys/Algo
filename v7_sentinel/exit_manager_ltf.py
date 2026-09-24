"""Exit Manager -- COMPONENT 2: LTF Exit Manager. Built 2026-09-22 (user's own
rule, given with a worked example):

  "buy open by TM or RM / price reaches H1 resistance / price touches / and
  LTF M1 makes a bearish cisd / close it"

Same overall TOUCH-then-CISD-CONFIRMATION mechanism RM-STR's own entry engine
uses (reversal_entry.py) -- a support/resistance touch arms a level in ITS
OWN implied direction (support -> bullish, resistance -> bearish), confirmed
with the user explicitly (PAIRED BY ROLE, not a direction-agnostic gate): only
a matching-direction CISD after that specific touch counts. Re-aimed here at
CLOSING instead of OPENING: "whenever price touches, and m1 creates an
opposite cisd after touching, then close the open trade at whatever profit or
loss it is." "Opposite" is relative to the OPEN POSITION -- a confirmed
bearish signal (resistance touch + bearish CISD) closes BUY positions, a
confirmed bullish signal (support touch + bullish CISD) closes SELL
positions -- across every manager (TM-STR/RM-STR/RM-ICT/SCALPER alike), same
no-exemptions scope Component 1 (exit_manager_bias.py) already uses.

M1 BACK (re-added 2026-09-23, same day it was removed -- user: "can we
add back this level touch plus m1 cisd confirmation as well"):
_CISD_TIMEFRAMES is (1, 3) again -- M1 tried first each cycle, M3 a
fallback when M1 doesn't qualify, same shape and same order as the
original M3-FALLBACK design. (Historical note: for a brief window the
same day this was M3-only, (3,) -- that shape is gone. See git history
around 2026-09-23 for the full back-and-forth.) The touch-arming
mechanism and the close-side validity check are unchanged throughout
all of this, only which CISD timeframe(s) get checked has moved.

CISD-CANDLE CLOSE-SIDE VALIDITY (added 2026-09-23, user's own worked
example: "4312 was 2h support, m1 bullish cisd close qualifying below
4312 is not valid, same m1 cisd qualifies to close the trade if the exit
qualifying price level is above 4312 level" -- same close-side principle
candle_touch.find_touching_line() already applies to EA-CandleExit/
Scalper, now applied here too): the confirming CISD candle's own CLOSE
(CISDState.close -- the close of that candle, not a wick) must sit on
the correct side of the SPECIFIC level being used to validate it -- above
the level's value for a bullish CISD (closes SELL), below it for a
bearish CISD (closes BUY). The touch itself can still be a bare wick
(scan_touches()/rt.store stay wick-aware, unchanged); it's specifically
the CISD candle that must have actually reclaimed/broken back past the
level by its own close, not just wicked through it, for that CISD to
count as a genuine confirmation off that level. A level whose touch is
armed but whose confirming CISD candle closed on the wrong side is not a
match -- another armed level (if any) may still qualify independently.

SCOPE -- "all supports and resistances of all timeframes till M15" (user's own
words) WIDENED 2026-09-24 to also include M10, M5, and M3 (user: "add M5 with
ATR and supertrend / add M3 with ATR and supertrend", then "add M10 as
well"): D1, H4, H2, H1, M30, M15, M10, M5, M3 -- htf_levels.compute_htf_state(),
the exact same ATR-dual + Supertrend level source RM-STR's own entry reads.
LTF_HTF_TIMEFRAMES below now includes one timeframe (M3) outside RM-STR's own
8-timeframe scope (htf_levels.HTF_TIMEFRAMES_MINUTES has no M3), so it's no
longer a strict subset of it either way. M10/M5/M3 are all bridge-only
(bridge.BRIDGE_ONLY_TIMEFRAMES) except M10, which is native copy_rates same as
D1-M30 -- either way, htf_levels.compute_htf_state() already handles the
branching, nothing extra needed here. Its own LevelEligibilityStore/
BridgeBarFlipTracker instances are SEPARATE from RM-STR's own (own state
files, see exit_manager_config.py) -- two independent processes reading the
same bridge/rates data, never sharing state.

MAIN POINT (user's own emphasis): "EM component not to execute its logic if
tp is set -- if tp is set then user is manually watching it, if tp is
removed, auto exit resumes, just like sl manager, and trade manager." Reuses
broker.has_manual_tp() exactly, the same per-position, fully reactive (not
latched) convention trade_manager.py already established -- a manual TP
placed on a position pauses ONLY that position's own auto-close here; every
other open position is unaffected, and removing the TP resumes evaluation
from whatever the current price/signal is the very next cycle, no special
resume step needed. Re-placing a TP pauses again. A Telegram alert fires on
every skip too (2026-09-22, "send me if exit manager tries exiting, when a
manual tp is placed so that i can understand the specified logic works"),
via telegram_alerts.send_if_configured().

"ALREADY-EXISTING CISD DOESN'T COUNT": same guard as Component 1 -- only a
position opened BEFORE the confirming M1/M3 candle's own CLOSE time
(cisd.bar_time + cisd_tf * 60) qualifies, so a position this exact same
signal just opened is never immediately closed by it.

Touch arming reuses reversal_entry.scan_touches() as-is (generic over any
htf_states dict + LevelEligibilityStore, nothing RM-STR-specific about its
own implementation) -- wick-aware via broker.price_extremes_since(), the same
mechanism RM-STR/RM-ICT already use so a touch shorter than one poll interval
still counts.

NOTE: unlike RM-STR's own entry use of LevelEligibilityStore, this component
NEVER calls mark_traded()/is_traded() for the "traded" concept -- there is no
"only once" rule here (a still-armed level legitimately protects again on a
LATER, separate M1/M3 CISD confirmation if a new opposite position has opened
in the meantime); only mark_touched()/is_touched() and sync()'s own
character-change invalidation are used. fresh_cisd()'s own one-shot-per-bar
contract is what already stops this from re-triggering every single poll
cycle within the same bar -- once that bar closes without a fresh
confirmation, fresh_cisd() goes back to None until a genuinely new one fires.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import MetaTrader5 as mt5

from v7_sentinel import broker, cisd_bridge, decision_log, htf_levels, telegram_alerts, trade_journal
from v7_sentinel.bridge_bar_flip import BridgeBarFlipTracker
from v7_sentinel.reversal_entry import scan_touches

if TYPE_CHECKING:
    from v7_sentinel.exit_manager_config import ExitManagerSymbolConfig, WatchedSource

_DIR_LABEL = {1: "BUY", -1: "SELL"}
_CISD_TIMEFRAMES = (1, 3)  # M1 tried first, M3 fallback (re-added 2026-09-23) -- see module docstring's "M1 BACK" section

# D1 through M15, plus M10, M5, and M3 (widened 2026-09-24, user's own words -- see
# module docstring's own SCOPE section).
LTF_HTF_TIMEFRAMES = (1440, 240, 120, 60, 30, 15, 10, 5, 3)


@dataclass
class LTFExitRuntime:
    """Per-symbol persisted state for this component -- its own store/tracker,
    separate from RM-STR's own (see module docstring)."""
    store: htf_levels.LevelEligibilityStore
    tracker: BridgeBarFlipTracker
    last_tick_msc: int = 0


def build_runtime(cfg: "ExitManagerSymbolConfig") -> LTFExitRuntime:
    return LTFExitRuntime(
        store=htf_levels.LevelEligibilityStore(cfg.ltf_levels_state_file),
        tracker=BridgeBarFlipTracker(cfg.ltf_bridge_bar_flip_state_file),
    )


def _compute_ltf_states(symbol: str, tracker: BridgeBarFlipTracker) -> dict[int, Optional[htf_levels.HTFState]]:
    return {tf: htf_levels.compute_htf_state(symbol, tf, tracker) for tf in LTF_HTF_TIMEFRAMES}


def _close_position(cfg: "ExitManagerSymbolConfig", position, source: "WatchedSource",
                    level_tf: int, level_source: str, cisd_tf: int, cisd_direction: str,
                    confirm_time: int) -> None:
    direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    print(f"[V7S-XM-LTF] closing {source.name} #{position.ticket} ({_DIR_LABEL[direction]}) -- "
          f"M{level_tf}/{level_source} touch + fresh M{cisd_tf} {cisd_direction} CISD confirmed after it opened")
    detail = {"rule": "HTF touch + M3 CISD (LTF exit)", "level_timeframe": level_tf,
              "level_source": level_source, "cisd_timeframe": cisd_tf, "cisd": cisd_direction,
              "cisd_confirm_time": confirm_time, "position_open_time": position.time}
    if not cfg.enable_trading:
        print("[V7S-XM-LTF] enable_trading is false -- decision only, no order sent")
        decision_log.log(cfg.decision_log_file, "ltf_close_decision_only", ticket=position.ticket,
                         target=source.name, direction=_DIR_LABEL[direction], **detail)
        return
    result = broker.close_position(cfg.symbol, position, cfg.deviation_points, comment="V7S-XM-LTF-SQ")
    if not result.ok:
        print(f"[V7S-XM-LTF] close failed: retcode={result.retcode} comment={result.comment}")
        decision_log.log(cfg.decision_log_file, "ltf_close_failed", ticket=position.ticket, target=source.name,
                         retcode=result.retcode)
        telegram_alerts.send_if_configured(
            cfg.alerts_bot_token, cfg.alerts_chat_id,
            f"[V7S] EXIT FAILED: {source.name} #{position.ticket} ({_DIR_LABEL[direction]}) -- "
            f"LTFEXIT (M{level_tf}/{level_source} touch + M{cisd_tf} {cisd_direction} CISD) -- "
            f"retcode={result.retcode} {result.comment}")
        return
    decision_log.log(cfg.decision_log_file, "ltf_close_filled", ticket=position.ticket, target=source.name,
                     direction=_DIR_LABEL[direction], **detail)
    trade_journal.TradeJournal(source.journal_file, source.name, cfg.symbol).exit_requested(
        position.ticket, "LTFEXIT", detail)
    telegram_alerts.send_if_configured(
        cfg.alerts_bot_token, cfg.alerts_chat_id,
        f"[V7S] EXIT: {source.name} #{position.ticket} ({_DIR_LABEL[direction]}) closed -- "
        f"LTFEXIT (M{level_tf}/{level_source} touch + fresh M{cisd_tf} {cisd_direction} CISD confirmed after it opened)")


def run_once(cfg: "ExitManagerSymbolConfig", rt: LTFExitRuntime) -> None:
    htf_states = _compute_ltf_states(cfg.symbol, rt.tracker)
    bid, ask = broker.get_tick_price(cfg.symbol)
    # Touch arming stays LIVE-tick (bid/ask against HTF levels, wick-aware) --
    # only the CISD confirmation itself is bar-close-gated.
    bid_low, ask_high = bid, ask
    if rt.last_tick_msc:
        lo, hi, newest = broker.price_extremes_since(cfg.symbol, rt.last_tick_msc)
        if lo is not None:
            bid_low, ask_high = min(bid, lo), max(ask, hi)
        rt.last_tick_msc = max(rt.last_tick_msc, newest)
    else:                                              # first cycle: start looking from now, never from history
        tick = mt5.symbol_info_tick(cfg.symbol)
        rt.last_tick_msc = int(tick.time_msc) if tick is not None else 0
    scan_touches(htf_states, rt.store, bid, ask, bid_low, ask_high)

    # M1 tried first; M3 only gets a chance if M1 has no fresh, matching-direction CISD this
    # cycle (see module docstring's own "M1 BACK" section) -- both share the same rt.store.
    for cisd_tf in _CISD_TIMEFRAMES:
        cisd = cisd_bridge.fresh_cisd(cfg.symbol, cisd_tf)
        if cisd is None:
            continue
        cisd_direction = cisd_bridge.direction_of(cisd)

        # Any ONE armed level in the SAME direction as this CISD is enough to
        # validate it -- see module docstring (no is_traded()/mark_traded()
        # gating here, unlike RM-STR's own entry use of this same store) --
        # AND the CISD candle's own close must have actually reclaimed/broken
        # back past THAT level (close-side validity, see module docstring).
        triggering = next(
            (
                (tf, level.source)
                for tf, state in htf_states.items() if state is not None
                for level in state.levels
                if (1 if level.role == "SUPPORT" else -1) == cisd_direction
                and rt.store.is_touched(tf, level.source, level.line_no, level.value, level.role)
                and ((cisd.close > level.value) if cisd_direction == 1 else (cisd.close < level.value))
            ),
            None,
        )
        if triggering is None:
            continue   # this CISD timeframe doesn't qualify -- fall through to the next one
        level_tf, level_source = triggering

        confirm_time = cisd.bar_time + cisd_tf * 60  # real close time of the confirming candle
        opposite_type = mt5.POSITION_TYPE_SELL if cisd_direction == 1 else mt5.POSITION_TYPE_BUY
        for source in cfg.sources:
            for position in broker.get_positions(cfg.symbol, source.magic_number):
                if position.type != opposite_type:
                    continue
                if position.time >= confirm_time:
                    continue   # opened at/after this CISD's own confirm time -- "already existing", not after
                own_tp = source.own_tp_lookup(position.ticket) if source.own_tp_lookup else None
                if broker.is_paused_by_manual_tp(position, own_tp):
                    direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
                    print(f"[V7S-XM-LTF] {source.name} #{position.ticket} has a manual TP set -- "
                          f"skipping auto-close (user is watching it manually)")
                    telegram_alerts.send_if_configured(
                        cfg.alerts_bot_token, cfg.alerts_chat_id,
                        f"[V7S] EM SKIPPED (manual TP set): {source.name} #{position.ticket} "
                        f"({_DIR_LABEL[direction]}) -- would have closed via LTFEXIT "
                        f"(M{level_tf}/{level_source} touch + M{cisd_tf} {cisd.last_cisd} CISD)")
                    continue
                _close_position(cfg, position, source, level_tf, level_source, cisd_tf, cisd.last_cisd, confirm_time)
