"""Configuration for V6-Sentinel's Reversal Manager (RM-STR + RM-ICT).
Ported from v5_sentinel/reversal_config.py (2026-09-18) but reshaped for
V6S's multi-instrument design (see config.py): instead of one flat
RMConfig read straight from global V5S_RM_* env vars, this module builds
one RMSymbolConfig PER SYMBOL, keyed off _SYMBOL_DEFAULTS below. Adding a
new symbol later means adding a deliberately-tuned entry to that dict
(the same "pull real tuned values, don't guess" approach used when V3's
crypto lineage added BTCUSD/ETHUSD) -- load_symbol_config() raises
clearly for any symbol without one, rather than silently reusing
XAUUSD's own thresholds for an instrument they were never tuned for.

MAGIC NUMBERS -- deliberately DIFFERENT from V5-Sentinel's own
(26090701/26090702), confirmed with the user 2026-09-18: V5S is still
running live on the same MT5 account, so V6S must never share a magic
number with it -- doing so would let V6S's own position-management code
(square-off, SL trailing, partial booking) see V5S's REAL live positions
as its own the moment it queries broker.get_positions(symbol, magic),
and potentially modify/close them once V6S's own trading is ever
enabled. V6S's RM-STR/RM-ICT use 26091801/26091802 instead.

Safety: enable_trading must be explicitly set to true (per-symbol env
var) for any order to actually be sent/modified/cancelled for THAT
symbol. Left unset (default false), every decision is printed but
nothing touches the account -- independently of any other component's
own trading flag.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from v6_sentinel import config

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class RMSymbolConfig:
    symbol: str
    lots: float
    deviation_points: int
    poll_seconds: float
    enable_trading: bool

    sl_buffer: float
    breakeven_trigger_points: float
    partial1_trigger_points: float
    partial1_fraction: float
    partial2_trigger_points: float
    partial2_fraction: float

    # STR component (HTF-levels-based, reversal_entry.py).
    magic_number: int
    state_file: str
    sl_state_file: str
    levels_state_file: str
    heartbeat_file: str

    # RM-ICT component (OB-zone-based, reversal_ict.py) -- FULLY
    # INDEPENDENT from STR (own magic number, own position slot, own
    # SL/Trade Manager state files, matching V5S's own confirmed design:
    # the two never square each other off or share a position).
    ict_magic_number: int
    ict_state_file: str
    ict_sl_state_file: str
    ict_eligibility_state_file: str

    # Shared NLB/NSB Block this symbol's RM reads (read-only) -- V6S's
    # own nlb_nsb_watcher, not V5S's (see nlb_nsb_block.py's own
    # docstring; V6S's Block is seeded from V5S's tv_scraper output but
    # is its own separate, V6S-owned store/file).
    nlb_nsb_block_state_file: str

    # ICT Guard -- applied to STR's own entries only (see ict_guard.py),
    # RM-ICT is deliberately exempt (its own entries are already sourced
    # FROM these exact zones).
    ict_guard_buffer_points: float
    ict_guard_sticky_state_file: str

    # RM-ICT SL-distance override (2026-09-19): when the zone-edge SL would
    # sit farther than this many points from entry, the SL comes from
    # M5/M3 lines (or CISD swing) instead -- see reversal_ict.py. Zone
    # size is not a condition. XAUUSD points; deliberately per-symbol config.
    ict_sl_override_points: float

    decision_log_file: str
    str_trade_journal_file: str        # per-trade entry/exit logic, one per component -- see trade_journal.py
    ict_trade_journal_file: str

    mt5_terminal_path: str | None
    mt5_login: int | None
    mt5_password: str | None
    mt5_server: str | None


# Deliberately-tuned defaults, one entry per symbol that's actually been
# tuned for RM-STR/RM-ICT -- XAUUSD's values are carried over unchanged
# from V5-Sentinel's own confirmed design (only the two magic numbers
# differ, see module docstring). Adding a new symbol means adding its
# OWN entry here after deliberate tuning, not inheriting XAUUSD's.
_SYMBOL_DEFAULTS: dict[str, dict] = {
    "XAUUSD": dict(
        lots=0.01,
        deviation_points=30,
        sl_buffer=2.0,
        breakeven_trigger_points=10.0,
        partial1_trigger_points=10.0,
        partial1_fraction=0.70,
        partial2_trigger_points=15.0,
        partial2_fraction=0.15,
        magic_number=26091801,
        ict_magic_number=26091802,
        ict_guard_buffer_points=5.0,
        ict_sl_override_points=15.0,
    ),
}


def load_symbol_config(symbol: str) -> RMSymbolConfig:
    """Builds this symbol's RMSymbolConfig from _SYMBOL_DEFAULTS, with
    every field overridable via a V6S_RM_{SYMBOL}_{FIELD} env var (e.g.
    V6S_RM_XAUUSD_LOTS). Raises KeyError with a clear message if this
    symbol has no tuned defaults yet -- deliberately no silent fallback
    to another symbol's thresholds."""
    if symbol not in _SYMBOL_DEFAULTS:
        raise KeyError(
            f"No RM tuning defaults for symbol {symbol!r} -- add a deliberately-tuned "
            f"entry to reversal_config._SYMBOL_DEFAULTS before enabling RM for this symbol."
        )
    d = _SYMBOL_DEFAULTS[symbol]
    prefix = f"V6S_RM_{symbol}_"

    return RMSymbolConfig(
        symbol=symbol,
        lots=float(os.getenv(prefix + "LOTS", str(d["lots"]))),
        deviation_points=int(os.getenv(prefix + "DEVIATION_POINTS", str(d["deviation_points"]))),
        poll_seconds=float(os.getenv(prefix + "POLL_SECONDS", str(config.POLL_SECONDS))),
        enable_trading=_env_bool(prefix + "ENABLE_TRADING", False),
        sl_buffer=float(os.getenv(prefix + "SL_BUFFER", str(d["sl_buffer"]))),
        breakeven_trigger_points=float(os.getenv(prefix + "BREAKEVEN_TRIGGER_POINTS", str(d["breakeven_trigger_points"]))),
        partial1_trigger_points=float(os.getenv(prefix + "PARTIAL1_TRIGGER_POINTS", str(d["partial1_trigger_points"]))),
        partial1_fraction=float(os.getenv(prefix + "PARTIAL1_FRACTION", str(d["partial1_fraction"]))),
        partial2_trigger_points=float(os.getenv(prefix + "PARTIAL2_TRIGGER_POINTS", str(d["partial2_trigger_points"]))),
        partial2_fraction=float(os.getenv(prefix + "PARTIAL2_FRACTION", str(d["partial2_fraction"]))),
        magic_number=int(os.getenv(prefix + "MAGIC_NUMBER", str(d["magic_number"]))),
        state_file=config.state_file_for("reversal_manager_state", symbol),
        sl_state_file=config.state_file_for("reversal_manager_sl_state", symbol),
        levels_state_file=config.state_file_for("reversal_manager_levels_state", symbol),
        heartbeat_file=config.state_file_for("reversal_manager_heartbeat", symbol),
        ict_magic_number=int(os.getenv(prefix + "ICT_MAGIC_NUMBER", str(d["ict_magic_number"]))),
        ict_state_file=config.state_file_for("reversal_manager_ict_state", symbol),
        ict_sl_state_file=config.state_file_for("reversal_manager_ict_sl_state", symbol),
        ict_eligibility_state_file=config.state_file_for("reversal_manager_ict_eligibility", symbol),
        nlb_nsb_block_state_file=config.state_file_for("nlb_nsb_block", symbol),
        ict_guard_buffer_points=float(os.getenv(prefix + "ICT_GUARD_BUFFER_POINTS", str(d["ict_guard_buffer_points"]))),
        ict_guard_sticky_state_file=config.state_file_for("reversal_manager_ict_guard_sticky", symbol),
        ict_sl_override_points=float(os.getenv(prefix + "ICT_SL_OVERRIDE_POINTS", str(d["ict_sl_override_points"]))),
        decision_log_file=config.state_file_for("reversal_manager_decision_log", symbol, ext="jsonl"),
        str_trade_journal_file=config.state_file_for("reversal_manager_str_trade_journal", symbol, ext="jsonl"),
        ict_trade_journal_file=config.state_file_for("reversal_manager_ict_trade_journal", symbol, ext="jsonl"),
        mt5_terminal_path=config.MT5_TERMINAL_PATH,
        mt5_login=config.MT5_LOGIN,
        mt5_password=config.MT5_PASSWORD,
        mt5_server=config.MT5_SERVER,
    )
