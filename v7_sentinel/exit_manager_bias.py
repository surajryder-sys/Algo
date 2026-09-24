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

MANUAL TP GUARD (fixed 2026-09-22 -- this component was MISSING it entirely until
now, even though the user's original "EM component not to execute its logic if
tp is set" instruction predates components 2/3, which both had it from their own
first build; only component 1 was never retrofitted, found while wiring the
Telegram skip-alert below): broker.has_manual_tp() pauses auto-close for a
position that currently carries a manual TP, same per-position, fully reactive
convention every other component/trade_manager.py uses. A Telegram alert
("EM SKIPPED (manual TP set)...") fires on every skip too, via
telegram_alerts.send_if_configured() -- so the user can directly observe the
guard working (or not) rather than inferring it from the absence of a close.

Applies per symbol; `sources` (exit_manager_config.WatchedSource) is only used to
know which journal file to write the closed trade's own "why" into.

SCALPER-ONLY M3/M5 VARIANT (run_once_scalper(), added 2026-09-23, user's own
words: "scalper trade can be exited when a m5/m3 opposite cisd event occurs"):
a SEPARATE, additional rule -- Scalper is already covered by the shared M5/M15
rule above (it's an ordinary entry in `sources`), but its own entries are
M3/M5 pattern-based (see scalper_entry.py), so this gives it a faster,
matching-granularity exit too: a fresh OPPOSITE M3 or M5 CISD closes ONLY
Scalper's own open positions. Every other manager (TM-STR/RM-STR/RM-ICT) is
completely unaffected -- this is not a broadening of the shared rule, just
one more independent watcher scoped to Scalper alone. Both variants share
the exact same underlying mechanics (_run()) -- fresh-CISD-after-entry,
manual-TP guard, decision log, Telegram alerts -- just different scope
(all sources vs. Scalper only), CISD timeframes, and log/comment/journal
tags, so the two never get confused for each other in any log.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

import MetaTrader5 as mt5

from v7_sentinel import broker, cisd_bridge, decision_log, telegram_alerts, trade_journal

if TYPE_CHECKING:
    from v7_sentinel.exit_manager_config import ExitManagerSymbolConfig, WatchedSource

_DIR_LABEL = {1: "BUY", -1: "SELL"}
CISD_TIMEFRAMES = (5, 15)          # M5 and M15, the SHARED rule -- every manager, either tf alone is sufficient
SCALPER_CISD_TIMEFRAMES = (3, 5)     # M3 and M5, SCALPER-ONLY (see module docstring)


def _source_for(cfg: "ExitManagerSymbolConfig", magic_number: int) -> "WatchedSource | None":
    return next((s for s in cfg.sources if s.magic_number == magic_number), None)


def _close_position(cfg: "ExitManagerSymbolConfig", position, source: "WatchedSource", tf_minutes: int,
                    cisd_direction: str, confirm_time: int, log_tag: str, reason: str, comment: str,
                    rule_desc: str) -> None:
    direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    print(f"[{log_tag}] closing {source.name} #{position.ticket} ({_DIR_LABEL[direction]}) -- "
          f"fresh M{tf_minutes} {cisd_direction} CISD confirmed after it opened")
    detail = {"rule": rule_desc, "timeframe": tf_minutes,
              "cisd": cisd_direction, "cisd_confirm_time": confirm_time, "position_open_time": position.time}
    if not cfg.enable_trading:
        print(f"[{log_tag}] enable_trading is false -- decision only, no order sent")
        decision_log.log(cfg.decision_log_file, f"{reason.lower()}_close_decision_only", ticket=position.ticket,
                         target=source.name, direction=_DIR_LABEL[direction], **detail)
        return
    result = broker.close_position(cfg.symbol, position, cfg.deviation_points, comment=comment)
    if not result.ok:
        print(f"[{log_tag}] close failed: retcode={result.retcode} comment={result.comment}")
        decision_log.log(cfg.decision_log_file, f"{reason.lower()}_close_failed", ticket=position.ticket,
                         target=source.name, retcode=result.retcode)
        telegram_alerts.send_if_configured(
            cfg.alerts_bot_token, cfg.alerts_chat_id,
            f"[V7S] EXIT FAILED: {source.name} #{position.ticket} ({_DIR_LABEL[direction]}) -- "
            f"{reason} (M{tf_minutes} {cisd_direction} CISD) -- retcode={result.retcode} {result.comment}")
        return
    decision_log.log(cfg.decision_log_file, f"{reason.lower()}_close_filled", ticket=position.ticket,
                     target=source.name, direction=_DIR_LABEL[direction], **detail)
    trade_journal.TradeJournal(source.journal_file, source.name, cfg.symbol).exit_requested(
        position.ticket, reason, detail)
    telegram_alerts.send_if_configured(
        cfg.alerts_bot_token, cfg.alerts_chat_id,
        f"[V7S] EXIT: {source.name} #{position.ticket} ({_DIR_LABEL[direction]}) closed -- "
        f"{reason} (fresh M{tf_minutes} {cisd_direction} CISD confirmed after it opened)")


def _run(cfg: "ExitManagerSymbolConfig", sources: Sequence["WatchedSource"], cisd_timeframes: tuple[int, ...],
        log_tag: str, reason: str, comment: str, rule_desc: str) -> None:
    for tf_minutes in cisd_timeframes:
        cisd = cisd_bridge.fresh_cisd(cfg.symbol, tf_minutes)
        if cisd is None:
            continue
        cisd_direction = cisd_bridge.direction_of(cisd)                       # 1 bullish, -1 bearish
        opposite_type = mt5.POSITION_TYPE_SELL if cisd_direction == 1 else mt5.POSITION_TYPE_BUY
        confirm_time = cisd.bar_time + tf_minutes * 60                        # real close time of the confirming candle
        for source in sources:
            for position in broker.get_positions(cfg.symbol, source.magic_number):
                if position.type != opposite_type:
                    continue
                if position.time >= confirm_time:
                    continue   # opened at/after this CISD's own confirm time -- "already existing", not after
                own_tp = source.own_tp_lookup(position.ticket) if source.own_tp_lookup else None
                if broker.is_paused_by_manual_tp(position, own_tp):
                    direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
                    print(f"[{log_tag}] {source.name} #{position.ticket} has a manual TP set -- "
                          f"skipping auto-close (user is watching it manually)")
                    telegram_alerts.send_if_configured(
                        cfg.alerts_bot_token, cfg.alerts_chat_id,
                        f"[V7S] EM SKIPPED (manual TP set): {source.name} #{position.ticket} "
                        f"({_DIR_LABEL[direction]}) -- would have closed via {reason} "
                        f"(fresh M{tf_minutes} {cisd.last_cisd} CISD)")
                    continue
                _close_position(cfg, position, source, tf_minutes, cisd.last_cisd, confirm_time,
                                log_tag=log_tag, reason=reason, comment=comment, rule_desc=rule_desc)


def run_once(cfg: "ExitManagerSymbolConfig") -> None:
    _run(cfg, cfg.sources, CISD_TIMEFRAMES, log_tag="V7S-XM-BIAS", reason="BIASEXIT", comment="V7S-XM-BIAS-SQ",
        rule_desc="opposite fresh M5/M15 CISD (bias exit)")


def run_once_scalper(cfg: "ExitManagerSymbolConfig") -> None:
    """See module docstring's own SCALPER-ONLY M3/M5 VARIANT section."""
    scalper_source = next((s for s in cfg.sources if s.name == "SCALPER"), None)
    if scalper_source is None:
        return
    _run(cfg, [scalper_source], SCALPER_CISD_TIMEFRAMES, log_tag="V7S-XM-SC", reason="SCALPEREXIT",
        comment="V7S-XM-SC-CISD-SQ", rule_desc="opposite fresh M3/M5 CISD (scalper-only exit)")
