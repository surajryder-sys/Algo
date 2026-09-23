"""Configuration for V6-Sentinel's Scalper manager. One ScalperSymbolConfig
PER SYMBOL, keyed off _SYMBOL_DEFAULTS -- same multi-instrument shape as
trend_config.py/reversal_config.py.

MAGIC NUMBER 26092201 -- its own, distinct from TM-STR (26091803), RM-STR
(26091801), RM-ICT (26091802). Same YYMMDDNN convention this project's
other magic numbers already use (built 2026-09-22, sequence 01).

RULE (user's own words, 2026-09-22): "now can we add one more manager,
called scalper / takes an entry with 0.05 lot size when a hammer or star
forms from support or resistance zones / places sl under or above candle,
for buy sell accordingly, with 2.0 buffer / seperate magic number, exit
manager can also close even this trade when exit criteria met / calculates
sl and places broker side tp for 1:1, or exits if EM satisfies thier
logics." See scalper_entry.py/scalper_main.py for the full mechanics --
this file is config only.

lots=0.05, sl_buffer=2.0 are the user's own explicit numbers, not tuned
defaults the way TM-STR's own lots/sl_buffer were -- kept here anyway for
the same env-var-overridable shape every other manager's config uses.

Safety: enable_trading must be explicitly set true (V6S_SCALPER_{SYMBOL}_
ENABLE_TRADING) for any order to actually be sent. Left unset (default
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
class ScalperSymbolConfig:
    symbol: str
    lots: float
    night_lots: float          # position_size_manager.py's own 23:00-04:00 IST window
    deviation_points: int
    poll_seconds: float
    enable_trading: bool

    sl_buffer: float          # points added beyond the pattern candle's own low/high
    risk_reward: float          # TP distance as a multiple of SL distance -- 1.0 (fixed 1:1)

    magic_number: int

    bridge_bar_flip_state_file: str   # M15 ATR flip tracker -- own instance, see scalper_main.py
    eligibility_state_file: str         # one trade per pattern event (bar_time + pattern_tf + direction)
    own_tp_state_file: str                # scalper_own_tp.ScalperOwnTPStore -- read by Exit Manager
    heartbeat_file: str
    decision_log_file: str
    trade_journal_file: str

    mt5_terminal_path: str | None
    mt5_login: int | None
    mt5_password: str | None
    mt5_server: str | None

    alerts_bot_token: str | None       # shared V6S Telegram bot, same one Exit Manager uses -- entry alerts
    alerts_chat_id: str | None


_SYMBOL_DEFAULTS: dict[str, dict] = {
    "XAUUSD": dict(
        lots=0.05,                # user's own explicit number
        night_lots=0.03,            # user's own explicit number, 2026-09-24 -- 23:00-04:00 IST, NOT a literal half of 0.05, see position_size_manager.py
        deviation_points=30,
        sl_buffer=2.0,              # user's own explicit number
        risk_reward=1.0,              # "1:1"
        magic_number=26092201,
    ),
}


def load_symbol_config(symbol: str) -> ScalperSymbolConfig:
    """Builds this symbol's ScalperSymbolConfig from _SYMBOL_DEFAULTS, with
    the scalar fields overridable via V6S_SCALPER_{SYMBOL}_{FIELD} env vars.
    Raises KeyError for a symbol with no tuned defaults -- deliberately no
    silent fallback, same convention every other manager's config uses."""
    if symbol not in _SYMBOL_DEFAULTS:
        raise KeyError(
            f"No Scalper tuning defaults for symbol {symbol!r} -- add a deliberately-tuned "
            f"entry to scalper_config._SYMBOL_DEFAULTS before enabling Scalper for this symbol."
        )
    d = _SYMBOL_DEFAULTS[symbol]
    prefix = f"V6S_SCALPER_{symbol}_"

    return ScalperSymbolConfig(
        symbol=symbol,
        lots=float(os.getenv(prefix + "LOTS", str(d["lots"]))),
        night_lots=float(os.getenv(prefix + "NIGHT_LOTS", str(d["night_lots"]))),
        deviation_points=int(os.getenv(prefix + "DEVIATION_POINTS", str(d["deviation_points"]))),
        poll_seconds=float(os.getenv(prefix + "POLL_SECONDS", str(config.POLL_SECONDS))),
        enable_trading=_env_bool(prefix + "ENABLE_TRADING", False),
        sl_buffer=float(os.getenv(prefix + "SL_BUFFER", str(d["sl_buffer"]))),
        risk_reward=float(os.getenv(prefix + "RISK_REWARD", str(d["risk_reward"]))),
        magic_number=int(os.getenv(prefix + "MAGIC_NUMBER", str(d["magic_number"]))),
        bridge_bar_flip_state_file=config.state_file_for("scalper_bridge_bar_flip", symbol),
        eligibility_state_file=config.state_file_for("scalper_eligibility", symbol),
        own_tp_state_file=config.state_file_for("scalper_own_tp", symbol),
        heartbeat_file=config.state_file_for("scalper_heartbeat", symbol),
        decision_log_file=config.state_file_for("scalper_decision_log", symbol, ext="jsonl"),
        trade_journal_file=config.state_file_for("scalper_trade_journal", symbol, ext="jsonl"),
        mt5_terminal_path=config.MT5_TERMINAL_PATH,
        mt5_login=config.MT5_LOGIN,
        mt5_password=config.MT5_PASSWORD,
        mt5_server=config.MT5_SERVER,
        alerts_bot_token=os.getenv("V6S_ALERTS_TELEGRAM_BOT_TOKEN") or None,
        alerts_chat_id=os.getenv("V6S_ALERTS_TELEGRAM_CHAT_ID") or None,
    )
