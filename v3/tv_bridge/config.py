"""Configuration for the TradingView webhook bridge, loaded from environment
variables (.env). Independent of the MT5-based bridges (ob_bridge/atr_bridge)
-- this one receives data over HTTP instead of reading MT5 Common Files.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class BridgeConfig:
    host: str
    port: int
    secret: str
    signal_log_file: str


def load_bridge_config() -> BridgeConfig:
    secret = os.getenv("TV_WEBHOOK_SECRET", "")
    if not secret:
        raise RuntimeError(
            "TV_WEBHOOK_SECRET is not set -- required so the public webhook "
            "endpoint can reject requests that aren't actually from your "
            "TradingView alerts. Set it in .env."
        )
    return BridgeConfig(
        host=os.getenv("TV_WEBHOOK_HOST", "127.0.0.1"),
        port=int(os.getenv("TV_WEBHOOK_PORT", "8765")),
        secret=secret,
        signal_log_file=os.getenv("TV_SIGNAL_LOG_FILE", "tv_bridge_signals.jsonl"),
    )
