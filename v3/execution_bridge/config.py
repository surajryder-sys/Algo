"""Configuration for Execution Bridge. Own config, separate from
v3/signal_engine's and v3/alert_manager's -- each bot/component in this
repo owns its own (see CLAUDE.md) -- but reuses the base
MT5_LOGIN/PASSWORD/SERVER/TERMINAL_PATH env vars (same already-running,
already-logged-in MetaTrader5-5 terminal every other bot here connects
to).

Reconciles TWO independent sources, each its own magic number/state
file/comment prefix -- Trend Manager and Reversal Manager can each hold
a position on the same symbol simultaneously (confirmed 2026-08-17,
"both can open same direction or opposite direction trades"), so they
need fully separate tracking, not just separate decisions. Lot sizes
and Stoploss Manager's trailing thresholds are shared across both
sources for the same symbol (not asked to differ per source -- assumed
the same trailing rule applies regardless of which system opened the
position, since the whole point of reusing Stoploss Manager wholesale
for Reversal Manager was "rest sl manager takes the job").
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class SymbolConfig:
    symbol: str
    lots: float
    # Stoploss Manager's point-based trailing thresholds, user's rule
    # 2026-08-18: breakeven once favor >= breakeven_points, trailing
    # starts at trail_start_points, moving in trail_step_points
    # increments from there. XAUUSD: 7/10/2 (two-stage -- a wider
    # dead zone between breakeven and trail start). BTCUSD/ETHUSD each
    # got their own explicit values from the user the same day (300/
    # 300/150 and 15/15/5 respectively) -- single-stage for both
    # (breakeven_points == trail_start_points), no longer a XAUUSD-
    # scaled placeholder.
    breakeven_points: float
    trail_start_points: float
    trail_step_points: float
    # Exit Manager's own points-based partial-booking tiers -- user's
    # rule 2026-08-26, given per symbol directly ("XAUUSD 50% at 10
    # points, another 25% at 20 points from entry, remaining 25% is
    # left for sl trail manager or bias exit" and so on for the other
    # four). Each (points, fraction) pair is absolute distance from
    # entry (favor points, same convention as Stoploss Manager's own
    # breakeven_points/trail_start_points) and the FRACTION of the
    # symbol's own fixed `lots` above to close at that point -- not a
    # fraction of whatever volume remains, so tier fractions across a
    # symbol's whole tuple plus whatever's left for Stoploss Manager's
    # own trailing always sum to 1.0. Sorted ascending by points so
    # exit_manager.py can fire them in the order price actually reaches
    # them, regardless of which order the user listed them in (e.g.
    # ETHUSD's own "50% close" tier is a LARGER point value than its
    # "25% close" tier, so the 25% one fires first in practice). Empty
    # tuple (none configured) means no partial booking for that symbol
    # -- position rides entirely on Stoploss Manager's own trailing,
    # the original/default behavior.
    partial_tiers: Tuple[Tuple[float, float], ...] = ()


@dataclass(frozen=True)
class SourceConfig:
    name: str            # "trend" or "reversal" -- for log lines only
    magic_number: int
    comment_prefix: str  # "TM" or "RM"
    decision_state_file: str  # the Manager's own state file (read-only)
    order_state_file: str     # Execution Bridge's own tracking for this source
    sl_state_file: str        # Stoploss Manager's own trailing state for this source
    exit_state_file: str      # Exit Manager's own partial-booking state for this source
    # Where a REAL manual cancel/close gets relayed back to this
    # source's own Manager (see manual_events.py) -- None for sources
    # that don't have that feedback loop wired up yet (Reversal
    # Manager doesn't consume this file at all currently, only Trend
    # Manager's trade_tracker.py does).
    manual_events_file: Optional[str] = None


@dataclass(frozen=True)
class Config:
    mt5_terminal_path: Optional[str]
    mt5_login: Optional[int]
    mt5_password: Optional[str]
    mt5_server: Optional[str]
    deviation_points: int
    poll_seconds: float
    enable_trading: bool
    sources: list  # list[SourceConfig]
    symbols: list  # list[SymbolConfig]


def load_config() -> Config:
    login_raw = os.getenv("MT5_LOGIN", "").strip()
    return Config(
        mt5_terminal_path=os.getenv("MT5_TERMINAL_PATH") or None,
        mt5_login=int(login_raw) if login_raw else None,
        mt5_password=os.getenv("MT5_PASSWORD") or None,
        mt5_server=os.getenv("MT5_SERVER") or None,
        deviation_points=int(os.getenv("EXECUTION_BRIDGE_DEVIATION_POINTS", "30")),
        poll_seconds=float(os.getenv("EXECUTION_BRIDGE_POLL_SECONDS", "2.0")),
        # Same convention as every other bot in this repo (see
        # CLAUDE.md): defaults false, nothing sends/cancels/modifies a
        # real order until this is explicitly set true in .env. Shared
        # by both sources -- one flag gates all real MT5 actions here.
        enable_trading=os.getenv("EXECUTION_BRIDGE_ENABLE_TRADING", "false").strip().lower() == "true",
        sources=[
            SourceConfig(
                name="trend",
                magic_number=int(os.getenv("TREND_MANAGER_MAGIC_NUMBER", "26081701")),
                comment_prefix="TM",
                decision_state_file=os.getenv("SIGNAL_ENGINE_TRADE_STATE_FILE", "trend_manager_trade_state.json"),
                order_state_file=os.getenv("EXECUTION_BRIDGE_ORDER_STATE_FILE", "execution_bridge_orders.json"),
                sl_state_file=os.getenv("EXECUTION_BRIDGE_SL_STATE_FILE", "execution_bridge_sl_state.json"),
                exit_state_file=os.getenv("EXECUTION_BRIDGE_EXIT_STATE_FILE", "execution_bridge_exit_state.json"),
                manual_events_file=os.getenv("EXECUTION_BRIDGE_MANUAL_EVENTS_FILE", "execution_bridge_manual_events.json"),
            ),
            SourceConfig(
                name="reversal",
                magic_number=int(os.getenv("REVERSAL_MANAGER_MAGIC_NUMBER", "26081801")),
                comment_prefix="RM",
                decision_state_file=os.getenv("REVERSAL_MANAGER_STATE_FILE", "reversal_manager_state.json"),
                order_state_file=os.getenv("EXECUTION_BRIDGE_REVERSAL_ORDER_STATE_FILE",
                                            "execution_bridge_orders_reversal.json"),
                sl_state_file=os.getenv("EXECUTION_BRIDGE_REVERSAL_SL_STATE_FILE",
                                         "execution_bridge_sl_state_reversal.json"),
                exit_state_file=os.getenv("EXECUTION_BRIDGE_REVERSAL_EXIT_STATE_FILE",
                                           "execution_bridge_exit_state_reversal.json"),
                # Confirmed live 2026-08-18: without this, a real SL hit
                # on a Reversal Manager position left its own state
                # showing FILLED forever, so Execution Bridge kept
                # re-opening a brand new position for it every cycle --
                # same class of bug already fixed for Trend Manager,
                # now closed here too.
                manual_events_file=os.getenv("EXECUTION_BRIDGE_REVERSAL_MANUAL_EVENTS_FILE",
                                              "execution_bridge_manual_events_reversal.json"),
            ),
        ],
        symbols=[
            SymbolConfig(
                "XAUUSD", float(os.getenv("EXECUTION_BRIDGE_XAUUSD_LOTS", "0.04")),
                breakeven_points=float(os.getenv("EXECUTION_BRIDGE_XAUUSD_BREAKEVEN_POINTS", "7")),
                trail_start_points=float(os.getenv("EXECUTION_BRIDGE_XAUUSD_TRAIL_START_POINTS", "10")),
                trail_step_points=float(os.getenv("EXECUTION_BRIDGE_XAUUSD_TRAIL_STEP_POINTS", "2")),
                # Partial-booking tiers, user's rule 2026-08-26: "50% at
                # 10 points, another 25% at 20 points from entry,
                # remaining 25% is left for sl trail manager or bias
                # exit." Already ascending by points.
                partial_tiers=((10.0, 0.5), (20.0, 0.25)),
            ),
            # BTCUSD/ETHUSD trailing -- user's explicit values 2026-08-18
            # ("SL Trailing for ETHUSD is 15 points up, put at cost, from
            # there every 5 points up, trail up" / "BTCUSD is 300 points
            # up, Put at cost, from there every 150 points, trail up") --
            # breakeven_points == trail_start_points for both (a single
            # threshold moves SL to cost AND starts stepping immediately,
            # no separate wider dead-zone the way XAUUSD's 7-then-10
            # two-stage version has). _desired_sl's existing formula
            # already handles breakeven==trail_start correctly with no
            # code change needed -- only the config values differ.
            SymbolConfig(
                "BTCUSD", float(os.getenv("EXECUTION_BRIDGE_BTCUSD_LOTS", "0.05")),
                breakeven_points=float(os.getenv("EXECUTION_BRIDGE_BTCUSD_BREAKEVEN_POINTS", "300")),
                trail_start_points=float(os.getenv("EXECUTION_BRIDGE_BTCUSD_TRAIL_START_POINTS", "300")),
                trail_step_points=float(os.getenv("EXECUTION_BRIDGE_BTCUSD_TRAIL_STEP_POINTS", "150")),
                # 900pts -> 0.03 lots, 1500pts -> 0.01 lots (user's own
                # final, unambiguous numbers, 2026-08-26 -- supersedes
                # an earlier "0.03 on 50%, remaining 0.02 split into two
                # parts for 25% each" version from the same day that
                # didn't cleanly resolve). At BTCUSD's own 0.05 lots
                # that's fractions of 0.6 and 0.2 respectively (NOT
                # literally 50%/25% -- chosen specifically to produce
                # these exact absolute amounts), leaving 0.01 (20%) for
                # the SL/bias manager, same two-tier-plus-remainder
                # shape every other symbol uses.
                partial_tiers=((900.0, 0.6), (1500.0, 0.2)),
            ),
            SymbolConfig(
                "ETHUSD", float(os.getenv("EXECUTION_BRIDGE_ETHUSD_LOTS", "1.0")),
                breakeven_points=float(os.getenv("EXECUTION_BRIDGE_ETHUSD_BREAKEVEN_POINTS", "15")),
                trail_start_points=float(os.getenv("EXECUTION_BRIDGE_ETHUSD_TRAIL_START_POINTS", "15")),
                trail_step_points=float(os.getenv("EXECUTION_BRIDGE_ETHUSD_TRAIL_STEP_POINTS", "5")),
                # 30pts -> 50%, 40pts -> 25%, remaining 25% to the
                # manager (2026-08-26, superseding an earlier ambiguous
                # 20/30 pair from the same day -- this is the user's own
                # corrected, unambiguous table).
                partial_tiers=((30.0, 0.5), (40.0, 0.25)),
            ),
            # USOIL/USTEC (added 2026-08-20) -- user's explicit values:
            # "initial sl as per parent ob, then as per point trailing
            # as of now, ob trailing will see later" -- so trailing is
            # plain point-based (same _desired_sl formula, no code
            # change), same single-stage shape as BTCUSD/ETHUSD
            # (breakeven_points == trail_start_points). USOIL's own
            # 0.600 up/0.600 trail and USTEC's 150 up/100 trail are
            # explicitly interim -- "will modify later according to
            # market movements".
            SymbolConfig(
                # Lots raised 0.02 -> 0.04, 2026-08-26 -- user's explicit
                # call, specifically so its partial-booking tiers land on
                # the same clean absolute amounts as XAUUSD's own (0.04
                # lots, 50%/25% -> 0.02/0.01). This is the symbol's own
                # EXECUTION lot size, not scoped to partial booking alone
                # -- every new USOIL entry (Trend or Reversal Manager)
                # places 0.04 lots from here on, not just the tiers below.
                "USOIL", float(os.getenv("EXECUTION_BRIDGE_USOIL_LOTS", "0.04")),
                breakeven_points=float(os.getenv("EXECUTION_BRIDGE_USOIL_BREAKEVEN_POINTS", "0.600")),
                trail_start_points=float(os.getenv("EXECUTION_BRIDGE_USOIL_TRAIL_START_POINTS", "0.600")),
                trail_step_points=float(os.getenv("EXECUTION_BRIDGE_USOIL_TRAIL_STEP_POINTS", "0.600")),
                # 2.0pts -> 50%, 3.0pts -> 25%, remaining 25% to the
                # manager (2026-08-26, superseding an earlier ambiguous
                # 1.0/2.0 pair from the same day -- this is the user's
                # own corrected, unambiguous table).
                partial_tiers=((2.0, 0.5), (3.0, 0.25)),
            ),
            SymbolConfig(
                # Lots raised 0.20 -> 1.0 earlier 2026-08-26 (so partial-
                # booking would land on clean 0.50/0.25 amounts), then
                # REVERTED back to 0.20 the same day -- user's explicit
                # correction: "ustec size needs to be 0.2, not 1.0, also
                # open trade i'll manage, just do the changes to lot
                # size." Only the lot size changed here, per that
                # instruction -- partial_tiers below still use the SAME
                # 0.5/0.25 fractions from the 1.0-lots version, so at
                # 0.20 lots they now produce 0.10/0.05 (not the
                # originally-intended 0.50/0.25) plus 0.05 remainder --
                # not recomputed, since the user asked specifically for
                # a lot-size-only change.
                "USTEC", float(os.getenv("EXECUTION_BRIDGE_USTEC_LOTS", "0.2")),
                breakeven_points=float(os.getenv("EXECUTION_BRIDGE_USTEC_BREAKEVEN_POINTS", "150")),
                trail_start_points=float(os.getenv("EXECUTION_BRIDGE_USTEC_TRAIL_START_POINTS", "150")),
                trail_step_points=float(os.getenv("EXECUTION_BRIDGE_USTEC_TRAIL_STEP_POINTS", "100")),
                # 200pts -> 50%, 300pts -> 25%, remaining 25% to the
                # manager (2026-08-26, superseding an earlier ambiguous
                # 100/200 pair from the same day -- this is the user's
                # own corrected, unambiguous table). Both now sit above
                # Stoploss Manager's own 150pt breakeven_points, so its
                # native trigger reaches breakeven first for USTEC
                # (unlike the earlier 100/200 pair) -- harmless either
                # way, both only ever move SL in the favorable
                # direction, never backward.
                partial_tiers=((200.0, 0.5), (300.0, 0.25)),
            ),
        ],
    )
