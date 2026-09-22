"""Exit Manager -- COMPONENT 1: Bias Exit Manager. Rebuilt from scratch 2026-09-21
(user: "lets re build the exit manager from the beginning"), replacing the earlier
entry-watching / timeframe-hierarchy design entirely.

RULE (user's own words): "whichever trade opened by whichever manager, an opposite
CISD on M5 or M15 will close the trade -- bearish CISD on M5/M15 closes all BUY
trades of all managers, bullish CISD on M5/M15 closes all SELL trades of all
managers." Confirmed: the trigger is a FRESH CISD confirmation (the "privileged,
momentary, only non-None the exact bar it confirmed" contract cisd_bridge.py
already uses everywhere else in this project), not the standing/current CISD.
Applies to EVERY manager's open positions -- TM-STR, RM-STR, RM-ICT alike, no
exemptions and no timeframe ranking between them (TM-STR keeps its own separate
bias-flip/M5-square-off logic on top of this; this is a second, independent
watcher of the SAME account).

"AN ALREADY-EXISTING CISD SHOULDN'T CLOSE THE TRADE" -- confirmed: only a CISD
whose confirming candle CLOSED AFTER the position was opened counts. A CISD event
is only real (confirmed) at that candle's CLOSE, so the comparison is
`cisd.bar_time + tf_minutes*60 > position.time`, not bar OPEN time -- same
close-time semantics trend_entry.find_flip_exit already uses for the identical
"only after entry" rule. This naturally covers both directions of the edge case:
  - A CISD that confirmed BEFORE a position opened never closes it, even while
    fresh_cisd() is still reporting it (that bar remains the latest closed one for
    the rest of its own length) -- it's "already existing" relative to that trade.
  - A CISD whose confirming candle closes SHORTLY AFTER a position opened (the
    candle was still forming when the trade opened) correctly DOES close it --
    the signal only became real at that close, which is after the position's own
    open time.

NO PERSISTED STATE NEEDED: unlike the old entry-watching design, this reacts
freshly every cycle from cisd_bridge + broker.get_positions -- a position that no
longer exists is simply not found again, and a process restart mid-bar
self-heals (fresh_cisd() is purely a live read, not an in-memory latch), so there
is nothing to persist or replay.

Applies per symbol; `sources` (exit_manager_config.WatchedSource) is only used to
know which journal file to write the closed trade's own "why" into.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import MetaTrader5 as mt5

from v6_sentinel import broker, cisd_bridge, decision_log, trade_journal

if TYPE_CHECKING:
    from v6_sentinel.exit_manager_config import ExitManagerSymbolConfig, WatchedSource

_DIR_LABEL = {1: "BUY", -1: "SELL"}
CISD_TIMEFRAMES = (5, 15)  # M5 and M15, checked independently every cycle -- either alone is sufficient


def _source_for(cfg: "ExitManagerSymbolConfig", magic_number: int) -> "WatchedSource | None":
    return next((s for s in cfg.sources if s.magic_number == magic_number), None)


def _close_position(cfg: "ExitManagerSymbolConfig", position, source: "WatchedSource", tf_minutes: int,
                    cisd_direction: str, confirm_time: int) -> None:
    direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    print(f"[V6S-XM-BIAS] closing {source.name} #{position.ticket} ({_DIR_LABEL[direction]}) -- "
          f"fresh M{tf_minutes} {cisd_direction} CISD confirmed after it opened")
    detail = {"rule": "opposite fresh M5/M15 CISD (bias exit)", "timeframe": tf_minutes,
              "cisd": cisd_direction, "cisd_confirm_time": confirm_time, "position_open_time": position.time}
    if not cfg.enable_trading:
        print("[V6S-XM-BIAS] enable_trading is false -- decision only, no order sent")
        decision_log.log(cfg.decision_log_file, "bias_close_decision_only", ticket=position.ticket,
                         target=source.name, direction=_DIR_LABEL[direction], **detail)
        return
    result = broker.close_position(cfg.symbol, position, cfg.deviation_points, comment="V6S-XM-BIAS-SQ")
    if not result.ok:
        print(f"[V6S-XM-BIAS] close failed: retcode={result.retcode} comment={result.comment}")
        decision_log.log(cfg.decision_log_file, "bias_close_failed", ticket=position.ticket, target=source.name,
                         retcode=result.retcode)
        return
    decision_log.log(cfg.decision_log_file, "bias_close_filled", ticket=position.ticket, target=source.name,
                     direction=_DIR_LABEL[direction], **detail)
    trade_journal.TradeJournal(source.journal_file, source.name, cfg.symbol).exit_requested(
        position.ticket, "BIASEXIT", detail)


def run_once(cfg: "ExitManagerSymbolConfig") -> None:
    for tf_minutes in CISD_TIMEFRAMES:
        cisd = cisd_bridge.fresh_cisd(cfg.symbol, tf_minutes)
        if cisd is None:
            continue
        cisd_direction = cisd_bridge.direction_of(cisd)                       # 1 bullish, -1 bearish
        opposite_type = mt5.POSITION_TYPE_SELL if cisd_direction == 1 else mt5.POSITION_TYPE_BUY
        confirm_time = cisd.bar_time + tf_minutes * 60                        # real close time of the confirming candle
        for source in cfg.sources:
            for position in broker.get_positions(cfg.symbol, source.magic_number):
                if position.type != opposite_type:
                    continue
                if position.time >= confirm_time:
                    continue   # opened at/after this CISD's own confirm time -- "already existing", not after
                _close_position(cfg, position, source, tf_minutes, cisd.last_cisd, confirm_time)
