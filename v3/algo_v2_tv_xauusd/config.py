"""Configuration for the TradingView-driven XAUUSD bot (Order Block + ATR
Trail zone rules), loaded from environment variables (.env). Runs alongside
algo_v2 (the MT5-indicator-driven XAUUSD bot) and the V1 algo/ bot on the
same terminal/account -- every state file, magic number, and block-status
filename here is namespaced separately so none of the three ever collide.

Reuses the same unprefixed MT5_LOGIN/PASSWORD/SERVER/TERMINAL_PATH as
algo_v2 -- this is deliberately the same account/terminal, not a separate
one (see project decision: "same XAUUSD").
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
class Config:
    symbol: str
    lots: float
    magic_number: int
    deviation_points: int
    poll_seconds: float
    enable_trading: bool
    state_file: str
    blocked_state_file: str
    sl_state_file: str
    atr_timeframe_minutes: int
    event_log_file: str
    # Currently-live zones only (added on ob_formed, removed on
    # ob_mitigated) -- distinct from event_log_file's append-only history.
    # See active_events.py's own docstring.
    active_events_file: str

    # Two independent, read-only data sources this bot merges (see
    # reader.py) -- tv_bridge/tradingview_bot's alert-fed files, and
    # tv_scraper's own separate files (kept separate deliberately: sharing
    # one file between the two writers would have them clobber each other's
    # zones -- see tv_scraper/config.py's comment on this). Neither is ever
    # written here.
    tv_zone_state_file: str
    tv_atr_state_file: str
    tv_scraper_zone_state_file: str
    tv_scraper_atr_state_file: str

    mt5_terminal_path: str | None
    mt5_login: int | None
    mt5_password: str | None
    mt5_server: str | None


def load_config() -> Config:
    login_raw = os.getenv("MT5_LOGIN", "").strip()

    return Config(
        symbol=os.getenv("TVX_SYMBOL", "XAUUSD"),
        lots=float(os.getenv("TVX_LOTS", "0.01")),
        magic_number=int(os.getenv("TVX_MAGIC_NUMBER", "26081101")),
        deviation_points=int(os.getenv("TVX_DEVIATION_POINTS", "30")),
        poll_seconds=float(os.getenv("TVX_POLL_SECONDS", "1")),
        enable_trading=_env_bool("TVX_ENABLE_TRADING", False),
        state_file=os.getenv("TVX_STATE_FILE", "tvx_bot_state.json"),
        blocked_state_file=os.getenv("TVX_BLOCKED_STATE_FILE", "tvx_bot_blocks.json"),
        sl_state_file=os.getenv("TVX_SL_STATE_FILE", "tvx_bot_sl_state.json"),
        atr_timeframe_minutes=int(os.getenv("TVX_ATR_TIMEFRAME_MINUTES", "5")),
        event_log_file=os.getenv("TVX_EVENT_LOG_FILE", "tvx_event_log.jsonl"),
        active_events_file=os.getenv("TVX_ACTIVE_EVENTS_FILE", "tvx_active_events.json"),
        tv_zone_state_file=os.getenv("TV_ZONE_STATE_FILE", "tradingview_bot_zones.json"),
        tv_atr_state_file=os.getenv("TV_ATR_STATE_FILE", "tradingview_bot_atr.json"),
        tv_scraper_zone_state_file=os.getenv("TV_SCRAPER_ZONE_STATE_FILE", "tv_scraper_zones.json"),
        tv_scraper_atr_state_file=os.getenv("TV_SCRAPER_ATR_STATE_FILE", "tv_scraper_atr.json"),
        mt5_terminal_path=os.getenv("MT5_TERMINAL_PATH") or None,
        mt5_login=int(login_raw) if login_raw else None,
        mt5_password=os.getenv("MT5_PASSWORD") or None,
        mt5_server=os.getenv("MT5_SERVER") or None,
    )
