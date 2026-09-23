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

TIMEFRAMES: M15 is the primary structure/gate, M5 the primary execution
timeframe. M3 ADDED 2026-09-22 as a second execution timeframe with its
own, STRICTER rule than M5's (see trend_entry.find_signal's own
docstring) -- M5's confirmed ATR state and the M15 gate must both be in
strict agreement with the direction before an M3 CISD may fire.

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
    # TYPE 2 booking (2026-09-23) -- wider trigger points used INSTEAD of the two above whenever
    # M5+M15 structure both agree with the open trade's own direction; same fractions either way.
    # See trade_manager.py's own module docstring for the full rule.
    type2_partial1_trigger_points: float
    type2_partial2_trigger_points: float

    magic_number: int
    bias_timeframe: int                    # M15
    execution_timeframes: tuple[int, ...]   # (5, 3) -- checked in this order each cycle
    trailing_timeframe: int                 # whose far ATR line the post-breakeven SL follows
    squareoff_timeframe: int                 # whose CISD + ATR strong/weak state can square off an open trade

    # Sideways Trapper, both M5 and M3 (user, 2026-09-22: "sideways trap to be followed by m3
    # as well", then "in both m3 and m5") -- after a SL_HIT, the next signal in that same
    # direction is skipped unless its own entry price is at least this many points away; see
    # sideways_trapper.py.
    sideways_trap_min_distance_points: float
    sideways_trapper_state_file: str

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
        type2_partial1_trigger_points=15.0,   # user's own explicit numbers, 2026-09-23
        type2_partial2_trigger_points=20.0,
        magic_number=26091803,
        bias_timeframe=15,
        execution_timeframes=(5, 3),   # M3 added 2026-09-22, its own stricter rule -- see trend_entry.py
        trailing_timeframe=5,
        squareoff_timeframe=5,
        sideways_trap_min_distance_points=5.0,
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
        type2_partial1_trigger_points=float(os.getenv(prefix + "TYPE2_PARTIAL1_TRIGGER_POINTS",
                                                      str(d["type2_partial1_trigger_points"]))),
        type2_partial2_trigger_points=float(os.getenv(prefix + "TYPE2_PARTIAL2_TRIGGER_POINTS",
                                                      str(d["type2_partial2_trigger_points"]))),
        magic_number=int(os.getenv(prefix + "MAGIC_NUMBER", str(d["magic_number"]))),
        bias_timeframe=d["bias_timeframe"],
        execution_timeframes=d["execution_timeframes"],
        trailing_timeframe=d["trailing_timeframe"],
        squareoff_timeframe=d["squareoff_timeframe"],
        sideways_trap_min_distance_points=float(os.getenv(prefix + "SIDEWAYS_TRAP_MIN_DISTANCE_POINTS",
                                                          str(d["sideways_trap_min_distance_points"]))),
        sideways_trapper_state_file=config.state_file_for("trend_manager_sideways_trapper", symbol),
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
