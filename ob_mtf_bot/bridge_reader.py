"""Reads the OB/FVG/Dynamic-Zone bridge file written by
mql5_utils/OB_Bridge_Aggregator.mq5.

The aggregator indicator scans every open chart for the symbol, reads OB
("pineBox") and FVG (BullFVG_/BearFVG_) rectangle objects plus the Dynamic
Zones formula, and writes one JSON snapshot to MT5's shared Common\\Files
folder. This is the read side of that handoff — independent of the bot's
own order-block re-detection in ob_detection.py.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BridgeOrderBlock:
    tf: str
    direction: str
    high: float
    low: float
    start_time: int
    start_time_str: str
    virgin: bool
    visit_time: int
    validation_time: int
    detected_time: int
    detected_price: float
    baseline: bool
    signature: str


@dataclass(frozen=True)
class BridgeFVG:
    tf: str
    direction: str
    high: float
    low: float
    created_time: int
    created_time_str: str
    active: bool
    retested: bool
    retest_time: int
    name: str


@dataclass(frozen=True)
class DynamicZones:
    day_open: float
    zone1_upper_5d: float
    zone2_upper_10d: float
    zone3_lower_5d: float
    zone4_lower_10d: float
    computed_at: int


@dataclass(frozen=True)
class BridgeState:
    generated_at: int
    generated_at_str: str
    symbol: str
    dynamic_zones: DynamicZones | None
    order_blocks: list[BridgeOrderBlock]
    fvgs: list[BridgeFVG]

    def order_blocks_for(self, tf: str) -> list[BridgeOrderBlock]:
        return [z for z in self.order_blocks if z.tf == tf]

    def fvgs_for(self, tf: str) -> list[BridgeFVG]:
        return [z for z in self.fvgs if z.tf == tf]


def bridge_file_path(filename: str = "ob_bridge_state.json") -> Path:
    """Resolves the shared Common\\Files path via the live terminal connection
    (mt5.initialize() must already have been called)."""
    info = mt5.terminal_info()
    if info is None:
        raise RuntimeError("MT5 terminal not initialized; call connection.connect() first")
    return Path(info.commondata_path) / "Files" / filename


def read_bridge_state(filename: str = "ob_bridge_state.json") -> BridgeState:
    path = bridge_file_path(filename)
    if not path.exists():
        raise FileNotFoundError(
            f"Bridge file not found at {path}. Is OB_Bridge_Aggregator attached to a chart?"
        )

    raw = json.loads(path.read_text())

    dz_raw = raw.get("dynamic_zones")
    dynamic_zones = DynamicZones(**dz_raw) if dz_raw else None

    order_blocks = [BridgeOrderBlock(**ob) for ob in raw.get("order_blocks", [])]
    fvgs = [BridgeFVG(**f) for f in raw.get("fvgs", [])]

    return BridgeState(
        generated_at=raw["generated_at"],
        generated_at_str=raw["generated_at_str"],
        symbol=raw["symbol"],
        dynamic_zones=dynamic_zones,
        order_blocks=order_blocks,
        fvgs=fvgs,
    )
