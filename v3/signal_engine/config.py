"""Configuration for Signal Engine's Managers (Trend Manager first).
Own small config, separate from v3/alert_manager/config.py's, even
though the symbol -> zone_state_file mapping is the same underlying
files -- each Manager/bot in this repo owns its own config rather than
importing another bot's (see CLAUDE.md), and Signal Engine is a peer to
Alert Manager, not a dependent of it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class SymbolConfig:
    symbol: str  # MT5 symbol name (plain, no broker suffix -- XAUUSD/BTCUSD/ETHUSD)
    zone_state_file: str  # tv_scraper's zone store for this symbol


@dataclass(frozen=True)
class Config:
    symbols: list  # list[SymbolConfig]
    poll_seconds: float


def load_config() -> Config:
    return Config(
        symbols=[
            SymbolConfig("XAUUSD", os.getenv("SIGNAL_ENGINE_XAUUSD_ZONE_FILE", "tv_scraper_xauusd_zones.json")),
            SymbolConfig("BTCUSD", os.getenv("SIGNAL_ENGINE_BTCUSD_ZONE_FILE", "tv_scraper_zones.json")),
            SymbolConfig("ETHUSD", os.getenv("SIGNAL_ENGINE_ETHUSD_ZONE_FILE", "tv_scraper_ethusd_zones.json")),
        ],
        poll_seconds=float(os.getenv("SIGNAL_ENGINE_POLL_SECONDS", "5.0")),
    )
