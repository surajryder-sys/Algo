"""Configuration for V5-Sentinel's STR Reversal Manager. Shares the same
symbol/lots/SL-buffer/breakeven/partial-booking values as Trend Manager's
own Config (confirmed 2026-09-07: "sl manager exactly works same...trade
manager also works same...partial booking also takes place exactly
same") by reading the SAME V5S_* env vars -- no duplication needed in
.env unless you want RM to actually diverge from TM on one of those.

Only RM-specific settings get their own V5S_RM_* vars: its own magic
number (separate identity from TM, confirmed 2026-09-07), its own
enable_trading toggle (independent go-ahead required, per this project's
per-bot safety convention -- RM going live is a SEPARATE decision from
TM's), and its own state files (so the two managers' persisted ticket/
level state never collide).

Safety: V5S_RM_ENABLE_TRADING must be explicitly set to true in .env for
any order to actually be sent/modified/cancelled. Left unset (default
false), every decision is printed but nothing touches the account --
independently of whatever V5S_ENABLE_TRADING (Trend Manager's own flag)
is set to.
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
class RMConfig:
    symbol: str
    lots: float
    magic_number: int
    deviation_points: int
    poll_seconds: float
    enable_trading: bool

    sl_buffer: float
    breakeven_trigger_points: float
    partial1_trigger_points: float
    partial1_fraction: float
    partial2_trigger_points: float
    partial2_fraction: float

    state_file: str
    sl_state_file: str
    levels_state_file: str
    # State file for BridgeBarFlipTracker (bar-close-gated M3 confirmation,
    # replaced the old live-tick BridgeFlipState 2026-09-09 -- that class
    # and its own state file are retired entirely, see reversal_entry.py's
    # own docstring for why).
    bridge_bar_flip_state_file: str
    heartbeat_file: str

    # RM-ICT (second component, 2026-09-09) -- reads the shared NLB/NSB
    # Block (read-only, owned/written by the separate nlb_nsb_watcher.py
    # process) and keeps its own eligibility bookkeeping entirely
    # separate from it, see reversal_ict.py's own docstring for why.
    # FULLY INDEPENDENT from the STR component (confirmed with the user
    # 2026-09-09, "no keep them both seperate... no interference") -- its
    # own magic number, own position slot, own SL/Trade Manager state
    # files, so the two can never square each other off or share a
    # position; STR's own magic_number/sl_state_file/state_file above
    # are untouched by this.
    nlb_nsb_block_state_file: str
    ict_eligibility_state_file: str
    ict_magic_number: int
    ict_sl_state_file: str
    ict_state_file: str

    mt5_terminal_path: str | None
    mt5_login: int | None
    mt5_password: str | None
    mt5_server: str | None


def load_config() -> RMConfig:
    login_raw = os.getenv("MT5_LOGIN", "").strip()

    return RMConfig(
        symbol=os.getenv("V5S_SYMBOL", "XAUUSD"),
        lots=float(os.getenv("V5S_LOTS", "0.01")),
        magic_number=int(os.getenv("V5S_RM_MAGIC_NUMBER", "26090701")),
        deviation_points=int(os.getenv("V5S_DEVIATION_POINTS", "30")),
        poll_seconds=float(os.getenv("V5S_POLL_SECONDS", "1")),
        enable_trading=_env_bool("V5S_RM_ENABLE_TRADING", False),
        sl_buffer=float(os.getenv("V5S_SL_BUFFER", "2.0")),
        breakeven_trigger_points=float(os.getenv("V5S_BREAKEVEN_TRIGGER_POINTS", "7")),
        partial1_trigger_points=float(os.getenv("V5S_PARTIAL1_TRIGGER_POINTS", "10")),
        partial1_fraction=float(os.getenv("V5S_PARTIAL1_FRACTION", "0.70")),
        partial2_trigger_points=float(os.getenv("V5S_PARTIAL2_TRIGGER_POINTS", "15")),
        partial2_fraction=float(os.getenv("V5S_PARTIAL2_FRACTION", "0.15")),
        state_file=os.getenv("V5S_RM_STATE_FILE", "v5s_reversal_manager_state.json"),
        sl_state_file=os.getenv("V5S_RM_SL_STATE_FILE", "v5s_reversal_manager_sl_state.json"),
        levels_state_file=os.getenv("V5S_RM_LEVELS_STATE_FILE", "v5s_reversal_manager_levels_state.json"),
        bridge_bar_flip_state_file=os.getenv("V5S_RM_BRIDGE_BAR_FLIP_STATE_FILE", "v5s_reversal_manager_bridge_bar_flip_state.json"),
        heartbeat_file=os.getenv("V5S_RM_HEARTBEAT_FILE", "v5s_reversal_manager_heartbeat.json"),
        nlb_nsb_block_state_file=os.getenv("V5S_NLB_NSB_BLOCK_STATE_FILE", "v5s_nlb_nsb_block.json"),
        ict_eligibility_state_file=os.getenv("V5S_RM_ICT_ELIGIBILITY_STATE_FILE", "v5s_reversal_manager_ict_eligibility.json"),
        ict_magic_number=int(os.getenv("V5S_RM_ICT_MAGIC_NUMBER", "26090702")),
        ict_sl_state_file=os.getenv("V5S_RM_ICT_SL_STATE_FILE", "v5s_reversal_manager_ict_sl_state.json"),
        ict_state_file=os.getenv("V5S_RM_ICT_STATE_FILE", "v5s_reversal_manager_ict_state.json"),
        mt5_terminal_path=os.getenv("MT5_TERMINAL_PATH") or None,
        mt5_login=int(login_raw) if login_raw else None,
        mt5_password=os.getenv("MT5_PASSWORD") or None,
        mt5_server=os.getenv("MT5_SERVER") or None,
    )
