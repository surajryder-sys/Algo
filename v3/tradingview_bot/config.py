"""Configuration for the TradingView bot, loaded from environment variables
(.env). Reads signals saved by tv_bridge.receiver; this first version only
logs/stores them -- no trading logic yet.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    signal_log_file: str
    state_file: str
    zone_state_file: str
    atr_state_file: str
    poll_seconds: float


def load_config() -> Config:
    return Config(
        signal_log_file=os.getenv("TV_SIGNAL_LOG_FILE", "tv_bridge_signals.jsonl"),
        state_file=os.getenv("TV_BOT_STATE_FILE", "tradingview_bot_state.json"),
        zone_state_file=os.getenv("TV_ZONE_STATE_FILE", "tradingview_bot_zones.json"),
        atr_state_file=os.getenv("TV_ATR_STATE_FILE", "tradingview_bot_atr.json"),
        poll_seconds=float(os.getenv("TV_BOT_POLL_SECONDS", "2")),
    )
