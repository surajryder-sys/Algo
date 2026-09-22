"""Configuration for V6-Sentinel's Exit Manager -- one ExitManagerSymbolConfig PER SYMBOL,
built from the trading components' own configs so the watched magic numbers and journal
files can never drift out of sync with the bots that own them.

WATCHED COMPONENTS (per symbol): TM-STR, RM-STR, RM-ICT. Each is a WatchedSource: its name
(the "component" string its trade journal uses), its magic number, and its trade journal file
(trade_journal.py) -- used by exit_manager_bias.py to know which journal to write a closed
trade's "why" into. TM-ICT is not built yet; when it is, it is one more entry in _sources_for().

Rebuilt 2026-09-21 (user: "lets re build the exit manager from the beginning") for
exit_manager_bias.py's Bias Exit Manager (component 1). No per-source ranking/timeframe
field any more -- that belonged to the earlier entry-watching design and no longer applies;
see exit_manager_bias.py's own docstring for the current rule.

ltf_levels_state_file / ltf_bridge_bar_flip_state_file (2026-09-22): component 2's
(exit_manager_ltf.py) OWN LevelEligibilityStore/BridgeBarFlipTracker state, deliberately
SEPARATE from RM-STR's own (reversal_config.py's levels_state_file/bridge_bar_flip_state_file)
-- two independent processes, never sharing state even though both read the same underlying
bridge/rates data.

candle_bridge_bar_flip_state_file (2026-09-22): component 3's (exit_manager_candle.py,
EA-CandleExit) OWN BridgeBarFlipTracker state -- also separate from every other component's
own tracker. No LevelEligibilityStore needed for component 3 at all (fully stateless design,
see that module's own docstring).

ict_block_state_file (2026-09-22, "add virgin zones as well from ict"): the SAME Block file
RM-ICT itself reads (reversal_config's own nlb_nsb_block_state_file) -- component 3 reads it,
never writes to it, exactly like RM-ICT's own read-only relationship to it.

Safety: enable_trading must be explicitly set true (V6S_EM_{SYMBOL}_ENABLE_TRADING) for any
close to actually be sent -- independent of every other component's own flag. Left unset
(default false), every decision is printed and logged but nothing touches the account.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from v6_sentinel import config, reversal_config, trend_config

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class WatchedSource:
    name: str              # the "component" string in that bot's trade journal, e.g. "RM-ICT"
    magic_number: int
    journal_file: str


@dataclass(frozen=True)
class ExitManagerSymbolConfig:
    symbol: str
    poll_seconds: float
    deviation_points: int
    enable_trading: bool
    sources: tuple[WatchedSource, ...]

    ltf_levels_state_file: str            # component 2 (LTF Exit Manager) -- own touch-arming store
    ltf_bridge_bar_flip_state_file: str     # component 2 -- own M15 ATR flip tracker
    candle_bridge_bar_flip_state_file: str    # component 3 (EA-CandleExit) -- own M15 ATR flip tracker
    ict_block_state_file: str                   # component 3 -- reads RM-ICT's own OB zone Block (read-only)

    heartbeat_file: str
    decision_log_file: str

    mt5_terminal_path: str | None
    mt5_login: int | None
    mt5_password: str | None
    mt5_server: str | None


def _sources_for(symbol: str) -> tuple[WatchedSource, ...]:
    tm = trend_config.load_symbol_config(symbol)
    rm = reversal_config.load_symbol_config(symbol)
    return (
        WatchedSource("TM-STR", tm.magic_number, tm.trade_journal_file),
        WatchedSource("RM-STR", rm.magic_number, rm.str_trade_journal_file),
        WatchedSource("RM-ICT", rm.ict_magic_number, rm.ict_trade_journal_file),
    )


def load_symbol_config(symbol: str) -> ExitManagerSymbolConfig:
    prefix = f"V6S_EM_{symbol}_"
    return ExitManagerSymbolConfig(
        symbol=symbol,
        poll_seconds=float(os.getenv(prefix + "POLL_SECONDS", str(config.POLL_SECONDS))),
        deviation_points=int(os.getenv(prefix + "DEVIATION_POINTS", "30")),
        enable_trading=_env_bool(prefix + "ENABLE_TRADING", False),
        sources=_sources_for(symbol),
        ltf_levels_state_file=config.state_file_for("exit_manager_ltf_levels", symbol),
        ltf_bridge_bar_flip_state_file=config.state_file_for("exit_manager_ltf_bridge_bar_flip", symbol),
        candle_bridge_bar_flip_state_file=config.state_file_for("exit_manager_candle_bridge_bar_flip", symbol),
        ict_block_state_file=reversal_config.load_symbol_config(symbol).nlb_nsb_block_state_file,
        heartbeat_file=config.state_file_for("exit_manager_heartbeat", symbol),
        decision_log_file=config.state_file_for("exit_manager_decision_log", symbol, ext="jsonl"),
        mt5_terminal_path=config.MT5_TERMINAL_PATH,
        mt5_login=config.MT5_LOGIN,
        mt5_password=config.MT5_PASSWORD,
        mt5_server=config.MT5_SERVER,
    )
