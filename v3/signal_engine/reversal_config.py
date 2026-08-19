"""Configuration for Reversal Manager. Own config, separate from Trend
Manager's (see CLAUDE.md -- each Manager owns its own), even though it
reads the exact same tv_scraper zone/live files.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple

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
    # Trend Manager's own two parent (bias) timeframes for this symbol --
    # None (default) means the M5-immediate/mitigation-close rules below
    # are NOT enabled for this symbol, keeping the original 2026-08-18
    # behavior (M5 always fires immediately, mitigation always closes).
    # Added 2026-08-19, user's explicit XAUUSD-only rule set:
    # - M5 retest whose direction agrees with AT LEAST ONE parent still
    #   fires immediately, same as before.
    # - M5 retest agreeing with NEITHER parent no longer fires OR gets
    #   dropped -- it's registered as a waiting retest instead, resolved
    #   by the SAME M1/M3/M5 LTF confirmation/invalidation machinery the
    #   HTF (H4/H2/H1/M30/M15) zones already use. SL for a
    #   confirmed-via-LTF fire naturally still comes from the M5 zone's
    #   own edge via the existing multi-waiting-zone SL logic in
    #   _check_direction -- no separate code path needed for that.
    # - Once filled, mitigation of the entry OB no longer auto-closes
    #   the trade -- only a fresh OPPOSITE-direction OB on M1 or M3 does
    #   (see _close_if_opposite_ltf_ob).
    parent_timeframes: Optional[Tuple[str, str]] = None
    # Hard cap on initial SL distance (price units, symbol's own scale)
    # -- None means uncomputed/no cap (BTCUSD/ETHUSD, unchanged). Added
    # same day as parent_timeframes above, same XAUUSD-only scope.
    max_sl_points: Optional[float] = None


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
                parent_timeframes=("5", "15"),
                max_sl_points=20.0,
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
