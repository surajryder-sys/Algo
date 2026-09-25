"""Configuration for V7-Sentinel's Exit Manager -- one ExitManagerSymbolConfig PER SYMBOL,
built from the trading components' own configs so the watched magic numbers and journal
files can never drift out of sync with the bots that own them.

WATCHED COMPONENTS (per symbol): TM-STR, RM-ICT. Each is a
WatchedSource: its name (the "component" string its trade journal uses), its magic number, and
its trade journal file (trade_journal.py) -- used by exit_manager_bias.py to know which journal
to write a closed trade's "why" into. TM-ICT is not built yet; when it is, it is one more entry
in _sources_for(). RM-STR (removed 2026-09-25) and SCALPER (removed 2026-09-25, along with
Scalper itself and its two dedicated Exit Manager components) used to be watched sources here
too.

own_tp_lookup (WatchedSource, 2026-09-22): a per-source optional ticket->tp lookup that every
Exit Manager closing loop can pass to broker.is_paused_by_manual_tp() instead of plain
has_manual_tp(), for any future manager that places its own broker-side TP at entry (Scalper,
removed 2026-09-25, was the only one that ever used this -- see broker.py's own
is_paused_by_manual_tp() docstring for why the distinction matters). No current source sets it.

Rebuilt 2026-09-21 (user: "lets re build the exit manager from the beginning") for
exit_manager_bias.py's Bias Exit Manager (component 1). No per-source ranking/timeframe
field any more -- that belonged to the earlier entry-watching design and no longer applies;
see exit_manager_bias.py's own docstring for the current rule.

ltf_levels_state_file / ltf_bridge_bar_flip_state_file (2026-09-22): component 2's
(exit_manager_ltf.py) OWN LevelEligibilityStore/BridgeBarFlipTracker state, deliberately
SEPARATE from every other component's own tracker/store, even though several read the same
underlying bridge/rates data.

ict_block_state_file (2026-09-22, "add virgin zones as well from ict"): the SAME Block file
RM-ICT itself reads (reversal_config's own nlb_nsb_block_state_file) -- component 3 (ICT Exit)
reads it too, never writes to it, exactly like RM-ICT's own read-only relationship to it, for
its own >M5 tier.

ict_ob_block_state_file (2026-09-24, ICT Exit): the merged TV+MT5 OB-zone store for M15/M5/M3
(ict_ob_block.py, Data Manager's own ict_ob_watcher.py) -- component 3's own fast (M3/M5) tier
data source, read-only, same file RM-ICT's new MT5-zone entries also read.

alerts_bot_token / alerts_chat_id (2026-09-22, renamed from zone_touch_alerts_* the same day
once the user asked for EXIT alerts on the same bot too -- "also send me exit alerts and
their logic too"): ONE shared Telegram bot for every alert-only (non-trading) notification
this component sends -- zone_touch_alert.py's own touch alerts AND the exit alert each
component's own _close_position() sends on every REAL fill. Left unset (both None, the
default with no env vars set) disables ALL of these at once -- same "unset means off"
convention enable_trading itself uses, but none of these ever send an order either way, only
a message. telegram_alerts.send_if_configured() is the shared guarded-send helper every one
of these call sites uses.

Safety: enable_trading must be explicitly set true (V7S_EM_{SYMBOL}_ENABLE_TRADING) for any
close to actually be sent -- independent of every other component's own flag. Left unset
(default false), every decision is printed and logged but nothing touches the account.
zone_touch_alert.py sends no orders at all, ever, so it is NOT gated by enable_trading.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Optional

from dotenv import load_dotenv

from v7_sentinel import config, reversal_config, trend_config

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
    own_tp_lookup: Optional[Callable[[int], Optional[float]]] = None   # SCALPER only, see module docstring


@dataclass(frozen=True)
class ExitManagerSymbolConfig:
    symbol: str
    poll_seconds: float
    deviation_points: int
    enable_trading: bool
    sources: tuple[WatchedSource, ...]

    ltf_levels_state_file: str            # component 2 (LTF Exit Manager) -- own touch-arming store
    ltf_bridge_bar_flip_state_file: str     # component 2 -- own M15 ATR flip tracker
    ict_block_state_file: str                   # component 3 -- reads RM-ICT's own OB zone Block (read-only)
    ict_ob_block_state_file: str                  # component 3 (ICT Exit) -- merged TV+MT5 M15/M5/M3 store (read-only)
    ict_exit_touch_max_age_minutes: float           # component 3 -- see exit_manager_ict.py's own TOUCH VALIDITY section
    ict_exit_bridge_bar_flip_state_file: str          # component 3 -- own M1 structure-flip tracker (2026-09-24)

    alerts_bot_token: str | None       # shared Telegram bot for zone-touch + exit alerts, see above
    alerts_chat_id: str | None

    heartbeat_file: str               # combined -- written by exit_manager.py's own standalone main()
    # Per-sub-component heartbeats (2026-09-24, V7S only) -- used by trade_manager_main.py's
    # consolidated loop for finer-grained Watchdog visibility than the single combined file above.
    heartbeat_file_bias: str
    heartbeat_file_ltf: str
    heartbeat_file_ict: str
    decision_log_file: str

    mt5_terminal_path: str | None
    mt5_login: int | None
    mt5_password: str | None
    mt5_server: str | None


def _sources_for(symbol: str) -> tuple[WatchedSource, ...]:
    tm = trend_config.load_symbol_config(symbol)
    rm = reversal_config.load_symbol_config(symbol)
    # RM-STR and SCALPER removed 2026-09-25 (see reversal_main.py's own docstring and
    # v7_sentinel's plan history for the Scalper removal) -- no longer watched sources.
    return (
        WatchedSource("TM-STR", tm.magic_number, tm.trade_journal_file),
        WatchedSource("RM-ICT", rm.ict_magic_number, rm.ict_trade_journal_file),
    )


def load_symbol_config(symbol: str) -> ExitManagerSymbolConfig:
    prefix = f"V7S_EM_{symbol}_"
    return ExitManagerSymbolConfig(
        symbol=symbol,
        poll_seconds=float(os.getenv(prefix + "POLL_SECONDS", str(config.POLL_SECONDS))),
        deviation_points=int(os.getenv(prefix + "DEVIATION_POINTS", "30")),
        enable_trading=_env_bool(prefix + "ENABLE_TRADING", False),
        sources=_sources_for(symbol),
        ltf_levels_state_file=config.state_file_for("exit_manager_ltf_levels", symbol),
        ltf_bridge_bar_flip_state_file=config.state_file_for("exit_manager_ltf_bridge_bar_flip", symbol),
        ict_block_state_file=reversal_config.load_symbol_config(symbol).nlb_nsb_block_state_file,
        ict_ob_block_state_file=config.state_file_for("ict_ob_block", symbol),
        ict_exit_touch_max_age_minutes=float(os.getenv(prefix + "ICT_EXIT_TOUCH_MAX_AGE_MINUTES", "30")),
        ict_exit_bridge_bar_flip_state_file=config.state_file_for("exit_manager_ict_bridge_bar_flip", symbol),
        alerts_bot_token=os.getenv("V7S_ALERTS_TELEGRAM_BOT_TOKEN") or None,
        alerts_chat_id=os.getenv("V7S_ALERTS_TELEGRAM_CHAT_ID") or None,
        heartbeat_file=config.state_file_for("exit_manager_heartbeat", symbol),
        heartbeat_file_bias=config.state_file_for("exit_manager_heartbeat_bias", symbol),
        heartbeat_file_ltf=config.state_file_for("exit_manager_heartbeat_ltf", symbol),
        heartbeat_file_ict=config.state_file_for("exit_manager_heartbeat_ict", symbol),
        decision_log_file=config.state_file_for("exit_manager_decision_log", symbol, ext="jsonl"),
        mt5_terminal_path=config.MT5_TERMINAL_PATH,
        mt5_login=config.MT5_LOGIN,
        mt5_password=config.MT5_PASSWORD,
        mt5_server=config.MT5_SERVER,
    )
