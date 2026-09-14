"""Configuration for Trend Manager's ICT component (TM-ICT). FULLY
INDEPENDENT from TM-STR's own Config (confirmed with the user
2026-09-14, same precedent as RM-STR/RM-ICT's own independence) -- its
own magic number, own position slot, own SL/Trade Manager state files,
own enable_trading toggle. Shares the same symbol/lots/deviation/poll
values and the same MT5 connection as every other V5-Sentinel component
by reading the SAME base V5S_* env vars, same convention
reversal_config.py already follows for RM.

Safety: V5S_TM_ICT_ENABLE_TRADING must be explicitly set to true in .env
for any order to actually be sent/modified/cancelled -- independent of
TM-STR's V5S_ENABLE_TRADING and RM's V5S_RM_ENABLE_TRADING.
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
class TMICTConfig:
    symbol: str
    lots: float
    magic_number: int
    deviation_points: int
    poll_seconds: float
    enable_trading: bool

    # OB-based initial SL buffer -- 2.0 points, own field (not TM-STR's
    # sl_buffer) per the user's own answer, 2026-09-14: "bearish trade ob
    # high with 2.0 buffer / bullish trade ob low with 2.0 buffer".
    ict_sl_buffer: float
    # Ongoing (Stage 2/3) far-line trailing buffer -- reuses the same
    # value/spirit as every other component's sl_buffer, own field so
    # it's independently tunable.
    trail_sl_buffer: float
    # Stage 3's own standalone points-in-favor breakeven trigger
    # (2026-09-15), ORed with partial-booking -- see ict_sl_manager.py's
    # own docstring. Reuses the SAME env var every other component's own
    # breakeven_trigger_points already reads.
    breakeven_trigger_points: float

    partial1_trigger_points: float
    partial1_fraction: float
    partial2_trigger_points: float
    partial2_fraction: float

    # ICT Guard -- SAME NLB/NSB Block every other component reads
    # (read-only, owned/written by the separate nlb_nsb_watcher.py
    # process), applied to TM-ICT's own entries too, 2026-09-14: "kindly
    # use NLB and NSB for this as well / the 5 points rule from
    # qualifying entry level" -- see trend_manager_ict.py's own docstring
    # for why this isn't circular the way RM-ICT's own exemption is.
    nlb_nsb_block_state_file: str
    ict_guard_buffer_points: float
    # Sticky-block memory (2026-09-14 -- see main.py's own Config field
    # of the same purpose and ict_guard.py's own docstring).
    ict_guard_sticky_state_file: str

    tv_zone_state_file: str    # same tv_scraper store every other component reads, read-only here
    block_state_file: str
    eligibility_state_file: str
    sl_state_file: str
    state_file: str
    heartbeat_file: str
    bridge_bar_flip_state_file: str
    decision_log_file: str

    mt5_terminal_path: str | None
    mt5_login: int | None
    mt5_password: str | None
    mt5_server: str | None


def load_config() -> TMICTConfig:
    login_raw = os.getenv("MT5_LOGIN", "").strip()

    return TMICTConfig(
        symbol=os.getenv("V5S_SYMBOL", "XAUUSD"),
        lots=float(os.getenv("V5S_LOTS", "0.01")),
        magic_number=int(os.getenv("V5S_TM_ICT_MAGIC_NUMBER", "26091401")),
        deviation_points=int(os.getenv("V5S_DEVIATION_POINTS", "30")),
        poll_seconds=float(os.getenv("V5S_POLL_SECONDS", "1")),
        enable_trading=_env_bool("V5S_TM_ICT_ENABLE_TRADING", False),
        ict_sl_buffer=float(os.getenv("V5S_TM_ICT_SL_BUFFER", "2.0")),
        trail_sl_buffer=float(os.getenv("V5S_TM_ICT_TRAIL_SL_BUFFER", "2.0")),
        breakeven_trigger_points=float(os.getenv("V5S_BREAKEVEN_TRIGGER_POINTS", "10")),
        partial1_trigger_points=float(os.getenv("V5S_PARTIAL1_TRIGGER_POINTS", "10")),
        partial1_fraction=float(os.getenv("V5S_PARTIAL1_FRACTION", "0.70")),
        partial2_trigger_points=float(os.getenv("V5S_PARTIAL2_TRIGGER_POINTS", "15")),
        partial2_fraction=float(os.getenv("V5S_PARTIAL2_FRACTION", "0.15")),
        nlb_nsb_block_state_file=os.getenv("V5S_NLB_NSB_BLOCK_STATE_FILE", "v5s_nlb_nsb_block.json"),
        ict_guard_buffer_points=float(os.getenv("V5S_ICT_GUARD_BUFFER_POINTS", "5.0")),
        ict_guard_sticky_state_file=os.getenv("V5S_TM_ICT_GUARD_STICKY_STATE_FILE",
                                              "v5s_tm_ict_guard_sticky.json"),
        tv_zone_state_file=os.getenv("V5S_TV_SCRAPER_ZONE_STATE_FILE", "v5s_tv_scraper_zones.json"),
        block_state_file=os.getenv("V5S_TM_ICT_BLOCK_STATE_FILE", "v5s_tm_ict_block.json"),
        eligibility_state_file=os.getenv("V5S_TM_ICT_ELIGIBILITY_STATE_FILE", "v5s_tm_ict_eligibility.json"),
        sl_state_file=os.getenv("V5S_TM_ICT_SL_STATE_FILE", "v5s_tm_ict_sl_state.json"),
        state_file=os.getenv("V5S_TM_ICT_STATE_FILE", "v5s_tm_ict_state.json"),
        heartbeat_file=os.getenv("V5S_TM_ICT_HEARTBEAT_FILE", "v5s_tm_ict_heartbeat.json"),
        bridge_bar_flip_state_file=os.getenv("V5S_TM_ICT_BRIDGE_BAR_FLIP_STATE_FILE",
                                             "v5s_tm_ict_bridge_bar_flip_state.json"),
        decision_log_file=os.getenv("V5S_TM_ICT_DECISION_LOG_FILE", "v5s_tm_ict_decision_log.jsonl"),
        mt5_terminal_path=os.getenv("MT5_TERMINAL_PATH") or None,
        mt5_login=int(login_raw) if login_raw else None,
        mt5_password=os.getenv("MT5_PASSWORD") or None,
        mt5_server=os.getenv("MT5_SERVER") or None,
    )
