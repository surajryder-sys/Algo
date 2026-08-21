"""Configuration for the Alert Manager, loaded from environment variables
(.env). Reuses the base MT5_LOGIN/PASSWORD/SERVER/TERMINAL_PATH -- the
same already-running, already-logged-in MetaTrader5-5 terminal
algo_v2/algo_v2_usoil_btc_eth already connect to (see
project_tv_scraper_multi_symbol_setup / project_v3_crypto_architecture
memory notes for why this terminal specifically) -- and the same
Telegram bot credentials the old, now-deleted algo/alerts.py used (see
project_virgin_zone_telegram_alerts memory).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class SymbolConfig:
    symbol: str  # MT5 symbol name (plain, no broker suffix -- XAUUSD/BTCUSD/ETHUSD)
    zone_state_file: str  # tv_scraper's zone store for this symbol


@dataclass(frozen=True)
class Config:
    mt5_terminal_path: Optional[str]
    mt5_login: Optional[int]
    mt5_password: Optional[str]
    mt5_server: Optional[str]
    telegram_bot_token: str
    telegram_chat_id: str
    poll_seconds: float
    symbols: list  # list[SymbolConfig]
    alerted_state_file: str
    # Raw tv_scraper timeframe strings (Pine's timeframe.period values --
    # "1"/"3"/"5"/"15"/"30"/"60"/"120"/"240") to never alert on, even if
    # the zone data is present. By explicit user request: M1/M3 excluded
    # -- currently only matters for XAUUSD, since BTCUSD/ETHUSD's own
    # tv_scraper grid was already reduced to 6 timeframes (no M1/M3 at
    # all -- see project_tv_scraper_multi_symbol_setup) before this
    # exclusion was even asked for.
    excluded_timeframes: frozenset
    # Minimum wall-clock seconds a zone must have been continuously
    # virgin+data-confirmed (see ConfirmationTracker) before Alert Manager
    # will fire on it, on top of (not instead of) the 2-distinct-write
    # data-quality confirmation. Added 2026-08-17 after several confirmed
    # reports (XAUUSD M30, ETHUSD H2, XAUUSD H1/M15/M5 simultaneously) of
    # alerts firing correctly against tv_scraper's own snapshot at that
    # instant, but the zone getting superseded/pushed out of the chart's
    # visible top-4 boxes within minutes -- by the time the user checked,
    # it looked like a phantom alert even though nothing was actually
    # wrong with the data at fire time. This trades a little latency for
    # only alerting on zones that stay visible long enough to verify.
    min_visible_seconds: float


def load_config() -> Config:
    login_raw = os.getenv("MT5_LOGIN", "").strip()
    excluded_raw = os.getenv("ALERT_MANAGER_EXCLUDED_TIMEFRAMES", "1,3")
    return Config(
        mt5_terminal_path=os.getenv("MT5_TERMINAL_PATH") or None,
        mt5_login=int(login_raw) if login_raw else None,
        mt5_password=os.getenv("MT5_PASSWORD") or None,
        mt5_server=os.getenv("MT5_SERVER") or None,
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        # Deliberately much faster than tv_scraper's own 5s Data Window
        # poll -- this is the whole point of checking MT5's live tick
        # feed instead of tv_scraper's own polled "retested" flag (see
        # project_v3_crypto_architecture's Alert Manager section).
        poll_seconds=float(os.getenv("ALERT_MANAGER_POLL_SECONDS", "1.0")),
        symbols=[
            SymbolConfig("XAUUSD", os.getenv("ALERT_MANAGER_XAUUSD_ZONE_FILE", "tv_scraper_xauusd_zones.json")),
            SymbolConfig("BTCUSD", os.getenv("ALERT_MANAGER_BTCUSD_ZONE_FILE", "tv_scraper_zones.json")),
            SymbolConfig("ETHUSD", os.getenv("ALERT_MANAGER_ETHUSD_ZONE_FILE", "tv_scraper_ethusd_zones.json")),
        ],
        alerted_state_file=os.getenv("ALERT_MANAGER_ALERTED_STATE_FILE", "alert_manager_alerted_zones.json"),
        excluded_timeframes=frozenset(t.strip() for t in excluded_raw.split(",") if t.strip()),
        min_visible_seconds=float(os.getenv("ALERT_MANAGER_MIN_VISIBLE_SECONDS", "180")),
    )
