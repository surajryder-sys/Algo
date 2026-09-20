"""Configuration for V6-Sentinel's Trend Manager (TM-STR). One
TMSymbolConfig PER SYMBOL, keyed off _SYMBOL_DEFAULTS below -- same
multi-instrument shape as reversal_config.py: adding a symbol means
adding a deliberately-tuned entry, and load_symbol_config() raises
clearly for any symbol without one rather than silently reusing
XAUUSD's thresholds.

MAGIC NUMBER 26091803 -- its own, distinct from RM's 26091801/26091802
and from V5-Sentinel's (TM-STR there was 26090201). V5S is still running
live on the same MT5 account, so V6S must never share a magic number
with it (see reversal_config.py's own docstring for why).

TIMEFRAMES (confirmed with the user 2026-09-20): M15 is the primary
structure and bias, M5 is the execution timeframe, M3 is a SECOND
execution timeframe to be added later -- execution_timeframes is a tuple
so that is a config change ((5,) -> (5, 3)) plus an SL-timeframe entry in
trend_entry.py, not a rewrite.

Safety: enable_trading must be explicitly set true (per-symbol env var)
for any order to actually be sent/modified/closed. Left unset (default
false), every decision is printed and logged but nothing touches the
account -- independently of every other component's own flag.
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
class TMSymbolConfig:
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

    magic_number: int
    bias_timeframe: int                    # M15
    execution_timeframes: tuple[int, ...]   # (5,) now; (5, 3) once M3 is added
    trailing_timeframe: int                 # whose far ATR line the post-breakeven SL follows
    squareoff_timeframe: int                 # whose CISD + ATR strong/weak state can square off an open trade

    state_file: str                # TradeManager (partial booking) state
    sl_state_file: str              # SLManager state
    eligibility_state_file: str      # one-trade-per-CISD-event memory
    bridge_bar_flip_state_file: str   # the M15 ATR-dual flip tracker
    heartbeat_file: str
    decision_log_file: str
    trade_journal_file: str            # per-trade entry/exit logic, see trade_journal.py

    mt5_terminal_path: str | None
    mt5_login: int | None
    mt5_password: str | None
    mt5_server: str | None


_SYMBOL_DEFAULTS: dict[str, dict] = {
    "XAUUSD": dict(
        lots=0.06,     # 0.06 (user, 2026-09-21): at 0.01 the 70%/15%/15% partial scheme degenerates -> 0.04/0.01/0.01
        deviation_points=30,
        sl_buffer=2.0,
        breakeven_trigger_points=10.0,
        partial1_trigger_points=10.0,
        partial1_fraction=0.70,
        partial2_trigger_points=15.0,
        partial2_fraction=0.15,
        magic_number=26091803,
        bias_timeframe=15,
        execution_timeframes=(5,),
        trailing_timeframe=5,
        squareoff_timeframe=5,
    ),
}


def load_symbol_config(symbol: str) -> TMSymbolConfig:
    """Builds this symbol's TMSymbolConfig from _SYMBOL_DEFAULTS, with the
    scalar fields overridable via V6S_TM_{SYMBOL}_{FIELD} env vars (e.g.
    V6S_TM_XAUUSD_LOTS). Raises KeyError for a symbol with no tuned
    defaults -- deliberately no silent fallback."""
    if symbol not in _SYMBOL_DEFAULTS:
        raise KeyError(
            f"No TM tuning defaults for symbol {symbol!r} -- add a deliberately-tuned "
            f"entry to trend_config._SYMBOL_DEFAULTS before enabling TM for this symbol."
        )
    d = _SYMBOL_DEFAULTS[symbol]
    prefix = f"V6S_TM_{symbol}_"

    return TMSymbolConfig(
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
        bias_timeframe=d["bias_timeframe"],
        execution_timeframes=d["execution_timeframes"],
        trailing_timeframe=d["trailing_timeframe"],
        squareoff_timeframe=d["squareoff_timeframe"],
        state_file=config.state_file_for("trend_manager_state", symbol),
        sl_state_file=config.state_file_for("trend_manager_sl_state", symbol),
        eligibility_state_file=config.state_file_for("trend_manager_eligibility", symbol),
        bridge_bar_flip_state_file=config.state_file_for("trend_manager_bridge_bar_flip_state", symbol),
        heartbeat_file=config.state_file_for("trend_manager_heartbeat", symbol),
        decision_log_file=config.state_file_for("trend_manager_decision_log", symbol, ext="jsonl"),
        trade_journal_file=config.state_file_for("trend_manager_trade_journal", symbol, ext="jsonl"),
        mt5_terminal_path=config.MT5_TERMINAL_PATH,
        mt5_login=config.MT5_LOGIN,
        mt5_password=config.MT5_PASSWORD,
        mt5_server=config.MT5_SERVER,
    )
