"""Configuration for Reversal Manager. Own config, separate from Trend
Manager's (see CLAUDE.md -- each Manager owns its own), even though it
reads the exact same tv_scraper zone/live files.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Tuple

from dotenv import load_dotenv

load_dotenv()

# Same five HTF timeframes for every symbol -- user's own list, no
# per-symbol variation given (unlike Trend Manager's parent timeframes).
HTF_TIMEFRAMES: Tuple[str, ...] = ("240", "120", "60", "30", "15")


@dataclass(frozen=True)
class SymbolConfig:
    symbol: str
    zone_state_file: str
    live_state_file: str
    # LTF confirmation timeframes -- XAUUSD has M1/M3/M5; BTCUSD/ETHUSD's
    # own tv_scraper grid has no M1/M3 at all (see
    # project_tv_scraper_multi_symbol_setup memory), so only M5 applies.
    ltf_timeframes: Tuple[str, ...]


@dataclass(frozen=True)
class Config:
    symbols: list  # list[SymbolConfig]
    poll_seconds: float
    state_file: str
    magic_number: int
    # Execution Bridge writes here (v3/execution_bridge/manual_events.py)
    # the moment it detects a REAL manual cancel/close or SL/TP hit for
    # a Reversal-Manager-sourced position -- read here, never written
    # here. Added 2026-08-18 after a real SL hit left this Manager's
    # own state showing FILLED forever with nothing real behind it.
    manual_events_file: str


def load_config() -> Config:
    return Config(
        symbols=[
            SymbolConfig(
                "XAUUSD",
                os.getenv("SIGNAL_ENGINE_XAUUSD_ZONE_FILE", "tv_scraper_xauusd_zones.json"),
                os.getenv("SIGNAL_ENGINE_XAUUSD_LIVE_FILE", "tv_scraper_xauusd_live.json"),
                ltf_timeframes=("1", "3", "5"),
            ),
            SymbolConfig(
                "BTCUSD",
                os.getenv("SIGNAL_ENGINE_BTCUSD_ZONE_FILE", "tv_scraper_zones.json"),
                os.getenv("SIGNAL_ENGINE_BTCUSD_LIVE_FILE", "tv_scraper_live.json"),
                ltf_timeframes=("5",),
            ),
            SymbolConfig(
                "ETHUSD",
                os.getenv("SIGNAL_ENGINE_ETHUSD_ZONE_FILE", "tv_scraper_ethusd_zones.json"),
                os.getenv("SIGNAL_ENGINE_ETHUSD_LIVE_FILE", "tv_scraper_ethusd_live.json"),
                ltf_timeframes=("5",),
            ),
        ],
        poll_seconds=float(os.getenv("REVERSAL_MANAGER_POLL_SECONDS", "5.0")),
        state_file=os.getenv("REVERSAL_MANAGER_STATE_FILE", "reversal_manager_state.json"),
        magic_number=int(os.getenv("REVERSAL_MANAGER_MAGIC_NUMBER", "26081801")),
        manual_events_file=os.getenv("EXECUTION_BRIDGE_REVERSAL_MANUAL_EVENTS_FILE",
                                      "execution_bridge_manual_events_reversal.json"),
    )
