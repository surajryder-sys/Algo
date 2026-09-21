"""Configuration for V6-Sentinel's Exit Manager -- one ExitManagerSymbolConfig PER SYMBOL,
built from the trading components' own configs so the watched magic numbers and journal
files can never drift out of sync with the bots that own them.

WATCHED COMPONENTS (per symbol): TM-STR, RM-STR, RM-ICT. Each is a WatchedSource: its name
(the "component" string its trade journal uses), its magic number, and its trade journal file
(trade_journal.py). TM-ICT is not built yet; when it is, it is one more entry in
_sources_for().

Safety: enable_trading must be explicitly set true (V6S_EM_{SYMBOL}_ENABLE_TRADING) for any
close to actually be sent -- independent of every other component's own flag. Left unset
(default false), every decision is printed and logged but nothing touches the account.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

from v6_sentinel import config, reversal_config, trend_config

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class WatchedSource:
    name: str              # the "component" string in that bot's trade journal, e.g. "RM-ICT"
    magic_number: int
    journal_file: str
    # A component whose trades all share one rank (TM-STR: its M15 bias/primary-structure timeframe)
    # sets it here; None means each trade's own timeframe is read from its journal entry (RM-STR: the
    # level's timeframe, RM-ICT: the zone's timeframe).
    fixed_timeframe_minutes: Optional[int] = None


@dataclass(frozen=True)
class ExitManagerSymbolConfig:
    symbol: str
    poll_seconds: float
    deviation_points: int
    enable_trading: bool
    sources: tuple[WatchedSource, ...]

    state_file: str
    heartbeat_file: str
    decision_log_file: str

    mt5_terminal_path: str | None
    mt5_login: int | None
    mt5_password: str | None
    mt5_server: str | None


def _sources_for(symbol: str) -> tuple[WatchedSource, ...]:
    tm = trend_config.load_symbol_config(symbol)
    rm = reversal_config.load_symbol_config(symbol)
    return (
        WatchedSource("TM-STR", tm.magic_number, tm.trade_journal_file, fixed_timeframe_minutes=tm.bias_timeframe),
        WatchedSource("RM-STR", rm.magic_number, rm.str_trade_journal_file),
        WatchedSource("RM-ICT", rm.ict_magic_number, rm.ict_trade_journal_file),
    )


def load_symbol_config(symbol: str) -> ExitManagerSymbolConfig:
    prefix = f"V6S_EM_{symbol}_"
    return ExitManagerSymbolConfig(
        symbol=symbol,
        poll_seconds=float(os.getenv(prefix + "POLL_SECONDS", str(config.POLL_SECONDS))),
        deviation_points=int(os.getenv(prefix + "DEVIATION_POINTS", "30")),
        enable_trading=_env_bool(prefix + "ENABLE_TRADING", False),
        sources=_sources_for(symbol),
        state_file=config.state_file_for("exit_manager_state", symbol),
        heartbeat_file=config.state_file_for("exit_manager_heartbeat", symbol),
        decision_log_file=config.state_file_for("exit_manager_decision_log", symbol, ext="jsonl"),
        mt5_terminal_path=config.MT5_TERMINAL_PATH,
        mt5_login=config.MT5_LOGIN,
        mt5_password=config.MT5_PASSWORD,
        mt5_server=config.MT5_SERVER,
    )
