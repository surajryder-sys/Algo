"""Configuration for the Exit Manager -- a standalone, cross-component
watcher (2026-09-15, user's own words: "lets build exit manager
seperately... that can watch everythting all components and take
decisions on making an exit"). See exit_manager.py's own docstring for
the full design.

Deliberately reads the SAME env vars each owning component already uses
for its own magic number and decision-log path (V5S_MAGIC_NUMBER,
V5S_RM_MAGIC_NUMBER, etc.) rather than a separate copy -- if the user
ever changes one of those, Exit Manager stays in sync automatically
instead of silently watching the wrong magic number.

Safety: V5S_EXIT_MANAGER_ENABLE_TRADING must be explicitly true in .env
for any close order to actually be sent -- independent of every other
component's own enable_trading flag, same per-bot convention as
everywhere else in this project.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class WatchedSource:
    name: str                       # "TM-STR" | "RM-STR" | "RM-ICT" | "TM-ICT"
    magic_number: int
    decision_log_file: str
    component_filter: str | None     # None, or "STR"/"ICT" to filter RM's own shared decision log


@dataclass(frozen=True)
class Config:
    symbol: str
    poll_seconds: float
    enable_trading: bool
    deviation_points: int

    sources: tuple[WatchedSource, ...]

    state_file: str            # per-source "last processed decision-log ts" -- see exit_manager.py
    decision_log_file: str      # Exit Manager's OWN log of what it closed and why
    heartbeat_file: str

    mt5_terminal_path: str | None
    mt5_login: int | None
    mt5_password: str | None
    mt5_server: str | None


def load_config() -> Config:
    login_raw = os.getenv("MT5_LOGIN", "").strip()

    sources = (
        WatchedSource(
            name="TM-STR",
            magic_number=int(os.getenv("V5S_MAGIC_NUMBER", "26090201")),
            decision_log_file=os.getenv("V5S_DECISION_LOG_FILE", "v5s_trend_manager_decision_log.jsonl"),
            component_filter=None,
        ),
        WatchedSource(
            name="RM-STR",
            magic_number=int(os.getenv("V5S_RM_MAGIC_NUMBER", "26090701")),
            decision_log_file=os.getenv("V5S_RM_DECISION_LOG_FILE", "v5s_reversal_manager_decision_log.jsonl"),
            component_filter="STR",
        ),
        WatchedSource(
            name="RM-ICT",
            magic_number=int(os.getenv("V5S_RM_ICT_MAGIC_NUMBER", "26090702")),
            decision_log_file=os.getenv("V5S_RM_DECISION_LOG_FILE", "v5s_reversal_manager_decision_log.jsonl"),
            component_filter="ICT",
        ),
        WatchedSource(
            name="TM-ICT",
            magic_number=int(os.getenv("V5S_TM_ICT_MAGIC_NUMBER", "26091401")),
            decision_log_file=os.getenv("V5S_TM_ICT_DECISION_LOG_FILE", "v5s_tm_ict_decision_log.jsonl"),
            component_filter=None,
        ),
    )

    return Config(
        symbol=os.getenv("V5S_SYMBOL", "XAUUSD"),
        poll_seconds=float(os.getenv("V5S_EXIT_MANAGER_POLL_SECONDS", os.getenv("V5S_POLL_SECONDS", "1"))),
        enable_trading=_env_bool("V5S_EXIT_MANAGER_ENABLE_TRADING", False),
        deviation_points=int(os.getenv("V5S_DEVIATION_POINTS", "30")),
        sources=sources,
        state_file=os.getenv("V5S_EXIT_MANAGER_STATE_FILE", "v5s_exit_manager_state.json"),
        decision_log_file=os.getenv("V5S_EXIT_MANAGER_DECISION_LOG_FILE", "v5s_exit_manager_decision_log.jsonl"),
        heartbeat_file=os.getenv("V5S_EXIT_MANAGER_HEARTBEAT_FILE", "v5s_exit_manager_heartbeat.json"),
        mt5_terminal_path=os.getenv("MT5_TERMINAL_PATH") or None,
        mt5_login=int(login_raw) if login_raw else None,
        mt5_password=os.getenv("MT5_PASSWORD") or None,
        mt5_server=os.getenv("MT5_SERVER") or None,
    )
