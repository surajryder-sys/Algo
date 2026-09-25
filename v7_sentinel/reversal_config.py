"""Configuration for V7-Sentinel's Reversal Manager (RM-ICT).
Ported from v5_sentinel/reversal_config.py (2026-09-18) but reshaped for
V7S's multi-instrument design (see config.py): instead of one flat
RMConfig read straight from global V5S_RM_* env vars, this module builds
one RMSymbolConfig PER SYMBOL, keyed off _SYMBOL_DEFAULTS below. Adding a
new symbol later means adding a deliberately-tuned entry to that dict
(the same "pull real tuned values, don't guess" approach used when V3's
crypto lineage added BTCUSD/ETHUSD) -- load_symbol_config() raises
clearly for any symbol without one, rather than silently reusing
XAUUSD's own thresholds for an instrument they were never tuned for.

RM-STR REMOVED (2026-09-25, user: "lets remove RM-STR completely, we
dont want reversal manager based based on ATR") -- this config used to
carry a full second set of fields for RM-STR (magic_number, state_file,
sl_state_file, levels_state_file, bridge_bar_flip_state_file,
sideways_trap_min_distance_points, sideways_trapper_state_file,
str_trade_journal_file), all gone now. Its own magic number, 26092402,
is retired -- never reused for anything else.

MAGIC NUMBERS -- deliberately DIFFERENT from V5-Sentinel's own
(26090701/26090702), confirmed with the user 2026-09-18: V5S is still
running live on the same MT5 account, so V7S must never share a magic
number with it -- doing so would let V7S's own position-management code
(square-off, SL trailing, partial booking) see V5S's REAL live positions
as its own the moment it queries broker.get_positions(symbol, magic),
and potentially modify/close them once V7S's own trading is ever
enabled. V7S's RM-ICT uses 26092403.

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

from v7_sentinel import config

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
    night_lots: float          # position_size_manager.py's own 23:00-04:00 IST window
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

    heartbeat_file: str
    decision_log_file: str

    # RM-ICT component (OB-zone-based, reversal_ict.py) -- the only Reversal Manager component now.
    ict_magic_number: int
    ict_state_file: str
    ict_sl_state_file: str
    ict_eligibility_state_file: str

    # Shared NLB/NSB Block this symbol's RM reads (read-only) -- V7S's
    # own nlb_nsb_watcher, not V5S's (see nlb_nsb_block.py's own
    # docstring; V7S's Block is seeded from the V7S Scraper's output but
    # is its own separate, V7S-owned store/file).
    nlb_nsb_block_state_file: str

    # Merged TV+MT5 OB-zone store for M15/M5/M3 (ict_ob_block.py, Data
    # Manager's own ict_ob_watcher.py) -- read-only. RM-ICT's own
    # find_ict_signals() scans this ALONGSIDE nlb_nsb_block_state_file
    # above (2026-09-24, "reversal trades... fired based on Mt5 zones as
    # well") -- see reversal_ict.py's own docstring for why this needs no
    # separate cross-source dedup mechanism of its own.
    ict_ob_block_state_file: str

    # ICT Guard -- deliberately not applied right now (see ict_guard.py /
    # reversal_main.py's own _open_position() docstring); `sticky` is
    # still threaded through so reinstating it later is a small change.
    ict_guard_buffer_points: float
    ict_guard_sticky_state_file: str

    # RM-ICT SL-distance override (2026-09-19): when the zone-edge SL would
    # sit farther than this many points from entry, the SL comes from
    # M5/M3 lines (or CISD swing) instead -- see reversal_ict.py. Zone
    # size is not a condition. XAUUSD points; deliberately per-symbol config.
    ict_sl_override_points: float

    # RM-ICT touch validity (user, 2026-09-21, after a live trade fired on an H4 zone
    # touched ~4 days earlier): a zone only counts as touched if the watcher saw price
    # enter it LIVE and that was at most this many minutes ago (30, same as V3's fix
    # for the same stale-retest bug). Seed-copied retests never count. See reversal_ict.py.
    ict_touch_max_age_minutes: float

    ict_trade_journal_file: str        # per-trade entry/exit logic -- see trade_journal.py

    mt5_terminal_path: str | None
    mt5_login: int | None
    mt5_password: str | None
    mt5_server: str | None

    alerts_bot_token: str | None       # shared V7S Telegram bot, same one Exit Manager uses -- entry alerts
    alerts_chat_id: str | None


# Deliberately-tuned defaults, one entry per symbol that's actually been
# tuned for RM-ICT -- XAUUSD's values are carried over unchanged from
# V5-Sentinel's own confirmed design (only the magic number differs, see
# module docstring). Adding a new symbol means adding its OWN entry here
# after deliberate tuning, not inheriting XAUUSD's.
_SYMBOL_DEFAULTS: dict[str, dict] = {
    "XAUUSD": dict(
        lots=0.06,     # 0.06 (user, 2026-09-21): at 0.01 the 70%/15%/15% partial scheme degenerates -> 0.04/0.01/0.01
        night_lots=0.03,   # user's own explicit number, 2026-09-24 -- 23:00-04:00 IST, see position_size_manager.py
        deviation_points=30,
        sl_buffer=2.0,
        breakeven_trigger_points=10.0,
        partial1_trigger_points=10.0,
        partial1_fraction=0.70,
        partial2_trigger_points=15.0,
        partial2_fraction=0.15,
        type2_partial1_trigger_points=15.0,   # user's own explicit numbers, 2026-09-23
        type2_partial2_trigger_points=20.0,
        ict_magic_number=26092403,   # V7S -- independent from V6S's 26091802
        ict_guard_buffer_points=5.0,
        ict_sl_override_points=15.0,
        ict_touch_max_age_minutes=30.0,
    ),
}


def load_symbol_config(symbol: str) -> RMSymbolConfig:
    """Builds this symbol's RMSymbolConfig from _SYMBOL_DEFAULTS, with
    every field overridable via a V7S_RM_{SYMBOL}_{FIELD} env var (e.g.
    V7S_RM_XAUUSD_LOTS). Raises KeyError with a clear message if this
    symbol has no tuned defaults yet -- deliberately no silent fallback
    to another symbol's thresholds."""
    if symbol not in _SYMBOL_DEFAULTS:
        raise KeyError(
            f"No RM tuning defaults for symbol {symbol!r} -- add a deliberately-tuned "
            f"entry to reversal_config._SYMBOL_DEFAULTS before enabling RM for this symbol."
        )
    d = _SYMBOL_DEFAULTS[symbol]
    prefix = f"V7S_RM_{symbol}_"

    return RMSymbolConfig(
        symbol=symbol,
        lots=float(os.getenv(prefix + "LOTS", str(d["lots"]))),
        night_lots=float(os.getenv(prefix + "NIGHT_LOTS", str(d["night_lots"]))),
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
        heartbeat_file=config.state_file_for("reversal_manager_heartbeat", symbol),
        decision_log_file=config.state_file_for("reversal_manager_decision_log", symbol, ext="jsonl"),
        ict_magic_number=int(os.getenv(prefix + "ICT_MAGIC_NUMBER", str(d["ict_magic_number"]))),
        ict_state_file=config.state_file_for("reversal_manager_ict_state", symbol),
        ict_sl_state_file=config.state_file_for("reversal_manager_ict_sl_state", symbol),
        ict_eligibility_state_file=config.state_file_for("reversal_manager_ict_eligibility", symbol),
        nlb_nsb_block_state_file=config.state_file_for("nlb_nsb_block", symbol),
        ict_ob_block_state_file=config.state_file_for("ict_ob_block", symbol),
        ict_guard_buffer_points=float(os.getenv(prefix + "ICT_GUARD_BUFFER_POINTS", str(d["ict_guard_buffer_points"]))),
        ict_guard_sticky_state_file=config.state_file_for("reversal_manager_ict_guard_sticky", symbol),
        ict_sl_override_points=float(os.getenv(prefix + "ICT_SL_OVERRIDE_POINTS", str(d["ict_sl_override_points"]))),
        ict_touch_max_age_minutes=float(os.getenv(prefix + "ICT_TOUCH_MAX_AGE_MINUTES", str(d["ict_touch_max_age_minutes"]))),
        ict_trade_journal_file=config.state_file_for("reversal_manager_ict_trade_journal", symbol, ext="jsonl"),
        mt5_terminal_path=config.MT5_TERMINAL_PATH,
        mt5_login=config.MT5_LOGIN,
        mt5_password=config.MT5_PASSWORD,
        mt5_server=config.MT5_SERVER,
        alerts_bot_token=os.getenv("V7S_ALERTS_TELEGRAM_BOT_TOKEN") or None,
        alerts_chat_id=os.getenv("V7S_ALERTS_TELEGRAM_CHAT_ID") or None,
    )
