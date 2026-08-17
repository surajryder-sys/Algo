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
from typing import Tuple

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class SymbolConfig:
    symbol: str  # MT5 symbol name (plain, no broker suffix -- XAUUSD/BTCUSD/ETHUSD)
    zone_state_file: str  # tv_scraper's zone store for this symbol
    # The two "parent" timeframes trend_manager.py compares -- whichever
    # has the newer eligible OB wins and opens the trade. Differs per
    # symbol: XAUUSD (M5/M15) vs BTCUSD/ETHUSD (M15/M30), per explicit
    # user request 2026-08-17 -- crypto's own tv_scraper grid only scrapes
    # H4/H2/H1/M30/M15/M5 (no M1/M3 at all, see
    # project_tv_scraper_multi_symbol_setup memory), so XAUUSD's M5/M3/M1
    # scheme simply doesn't apply there; everything shifts one tier up.
    parent_timeframes: Tuple[str, str]
    # Pure execution triggers -- never get their own watermark, just
    # need ANY confirmed OB in the parent's direction to fire. XAUUSD:
    # M5/M3/M1 ("whichever forms first"). BTCUSD/ETHUSD: M15/M5 (same
    # "whichever gets the early entry" idea, shifted for the TFs crypto
    # actually has).
    trigger_timeframes: Tuple[str, ...]


@dataclass(frozen=True)
class Config:
    symbols: list  # list[SymbolConfig]
    poll_seconds: float
    trade_state_file: str


def load_config() -> Config:
    return Config(
        symbols=[
            SymbolConfig(
                "XAUUSD",
                os.getenv("SIGNAL_ENGINE_XAUUSD_ZONE_FILE", "tv_scraper_xauusd_zones.json"),
                parent_timeframes=("5", "15"),
                trigger_timeframes=("5", "3", "1"),
            ),
            SymbolConfig(
                "BTCUSD",
                os.getenv("SIGNAL_ENGINE_BTCUSD_ZONE_FILE", "tv_scraper_zones.json"),
                parent_timeframes=("15", "30"),
                trigger_timeframes=("15", "5"),
            ),
            SymbolConfig(
                "ETHUSD",
                os.getenv("SIGNAL_ENGINE_ETHUSD_ZONE_FILE", "tv_scraper_ethusd_zones.json"),
                parent_timeframes=("15", "30"),
                trigger_timeframes=("15", "5"),
            ),
        ],
        poll_seconds=float(os.getenv("SIGNAL_ENGINE_POLL_SECONDS", "5.0")),
        trade_state_file=os.getenv("SIGNAL_ENGINE_TRADE_STATE_FILE", "trend_manager_trade_state.json"),
    )
