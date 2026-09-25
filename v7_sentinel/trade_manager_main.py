"""V7-Sentinel Trade Manager -- the consolidated trading process.

Built 2026-09-24 as part of the V6S -> V7S architecture consolidation: a
real incident found 4 of V6-Sentinel's 6 processes (Exit Manager, Trend
Manager, Scalper, NLB/NSB Watcher) silently broken for 10+ hours on a
stale MT5 Python API IPC connection ("IPC send failed"), completely
undetected by their own heartbeats (which write unconditionally,
regardless of error) -- only Reversal Manager's own SEPARATE connection
stayed healthy the whole time. Root cause: independent processes mean
independent MT5 connections, any one of which can silently degrade
while the others look fine.

This file structurally removes that failure mode for the trading side:
TM-STR, RM-ICT, and Exit Manager's own components (+ its zone-touch
alert) all run in ONE process on ONE shared MT5 connection instead of
several separate ones. `broker.py` is a stateless wrapper around the
process-global `MetaTrader5` package -- `mt5.
initialize()`/`shutdown()` are already documented as idempotent, and
every V6S manager already relies on that (each connects once per
SYMBOL in `config.ACTIVE_SYMBOLS`, sharing one connection across
several logical instances within its own process) -- this is the exact
same pattern, one level up, across SUB-COMPONENTS instead of symbols.

REUSE, NOT REWRITE: `trend_main.py`, `reversal_main.py`,
and every `exit_manager_*.py` component keep their own `_build_runtime()`/
`run_once()` functions completely unchanged -- this file only imports and
orchestrates them. Each of those files' own standalone `main()`/`while
True`/`broker.connect()+shutdown()` is left fully intact too (unused when
run via this consolidated entry point, but still there for isolated
debugging if ever needed -- `python -m v7_sentinel.trend_main` still works
on its own, just isn't how this process is normally run).

ORDER PER CYCLE: entries first (TM-STR, RM-ICT), then Exit
Manager's components (which only ever CLOSE). Today's separate V6S
processes race with no defined relative order at all; this is strictly
more deterministic, not a behavior change worth flagging beyond this
note. Each sub-component call is independently try/except-guarded (see
_run_guarded()) so one sub-component's exception can never block the
others in the same cycle -- notably FIXES a real, pre-existing gap in
exit_manager.py's own run_once() (its components run back-to-back with
NO try/except between them there, only one level up around the whole
symbol; found while designing this file, see the V7S consolidation plan).

PER-SUB-COMPONENT HEARTBEATS (2026-09-24, user's own choice over V6S's
coarser 4-file scheme): 3 heartbeat files -- TM-STR, RM-ICT,
EM-ICT -- written right after each guarded call, success or
caught exception, same "written unconditionally" convention as every
other heartbeat in this project (a hung/dead sub-component is what
heartbeat staleness catches; a raised-and-caught exception is what this
component's own run.log + Watchdog's failure-signature check catches).
RM-STR's own heartbeat retired 2026-09-25 along with the rest of it;
Scalper, EM-Candle, and EM-Scalper-M3M5's heartbeats retired 2026-09-25
along with Scalper itself (magic 26092404 is retired, never reused, same
convention as RM-STR's 26092402); EM-LTF's own heartbeat retired
2026-09-25 too, on request (see exit_manager.py's own docstring for what
else went with it); EM-Bias's own heartbeat retired 2026-09-25 too, on
request (user: "cisd is only for entry, we are taking out of exit") --
ICT Exit is now Exit Manager's only component. This only fully protects
against a RAISED exception, not a true infinite-loop hang inside one
sub-component -- a genuine hang inside `fn()` would stall every LATER
sub-component in that same cycle too, since all 3 run sequentially in one
thread; the only way around that is a per-call timeout, which is a real
feature addition beyond this consolidation, not built here.

Run with: python -m v7_sentinel.trade_manager_main
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from v7_sentinel import (
    broker,
    config,
    exit_manager_ict,
    heartbeat,
    reversal_main,
    trend_main,
    zone_touch_alert,
)
from v7_sentinel.exit_manager_config import load_symbol_config as load_em_config


@dataclass
class _SymbolRuntime:
    symbol: str
    tm: "trend_main._SymbolRuntime"
    rm: "reversal_main._SymbolRuntime"
    em_cfg: "object"
    em_ict: "exit_manager_ict.ICTExitRuntime"
    em_zone_alert: "zone_touch_alert.ZoneTouchAlertRuntime"


def _build_runtime(symbol: str) -> _SymbolRuntime:
    em_cfg = load_em_config(symbol)
    return _SymbolRuntime(
        symbol=symbol,
        tm=trend_main._build_runtime(symbol),
        rm=reversal_main._build_runtime(symbol),
        em_cfg=em_cfg,
        em_ict=exit_manager_ict.build_runtime(em_cfg),
        em_zone_alert=zone_touch_alert.build_runtime(),
    )


def _run_guarded(tag: str, heartbeat_file: str, fn: Callable[[], None]) -> None:
    """One sub-component's own cycle -- an exception here is caught, logged,
    and never allowed to block any other sub-component's own call this
    cycle. heartbeat_file is written unconditionally afterward (success or
    caught exception), same convention every heartbeat in this project
    already uses -- see module docstring's own PER-SUB-COMPONENT HEARTBEATS
    section for exactly what this does and doesn't protect against."""
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 -- one sub-component's failure must never block the rest
        print(f"[V7S-TR] {tag} cycle error: {exc!r}")
    heartbeat.write(heartbeat_file)


def run_once(rt: _SymbolRuntime) -> None:
    # Entries first (today's separate V6S processes race with no defined order at all --
    # this is strictly more deterministic, see module docstring).
    _run_guarded("TM-STR", rt.tm.cfg.heartbeat_file, lambda: trend_main.run_once(rt.tm))
    _run_guarded("RM-ICT", rt.rm.cfg.heartbeat_file, lambda: reversal_main.run_once(rt.rm))

    # Then Exit Manager's own component -- only ever CLOSEs, never opens.
    _run_guarded("EM-ICT", rt.em_cfg.heartbeat_file_ict, lambda: exit_manager_ict.run_once(rt.em_cfg, rt.em_ict))

    # Alert-only, sends no orders -- no heartbeat needed (not gated by enable_trading either).
    try:
        zone_touch_alert.run_once(rt.em_cfg, rt.em_zone_alert)
    except Exception as exc:  # noqa: BLE001
        print(f"[V7S-TR] ZONE-ALERT cycle error: {exc!r}")


def main() -> None:
    runtimes = [_build_runtime(symbol) for symbol in config.ACTIVE_SYMBOLS]
    for rt in runtimes:
        print(f"[V7S-TR] {rt.symbol} starting -- TM-STR magic={rt.tm.cfg.magic_number} "
              f"RM-ICT magic={rt.rm.cfg.ict_magic_number} -- "
              f"enable_trading TM={rt.tm.cfg.enable_trading} RM={rt.rm.cfg.enable_trading} "
              f"EM={rt.em_cfg.enable_trading}")
        # One shared connection per symbol -- mt5.initialize() is idempotent, every V6S manager
        # already relies on this (see module docstring). Reusing TM's own cfg for the connect
        # call is arbitrary -- every sub-component's config points at the same MT5 account.
        broker.connect(rt.symbol, rt.tm.cfg.mt5_terminal_path, rt.tm.cfg.mt5_login,
                       rt.tm.cfg.mt5_password, rt.tm.cfg.mt5_server)
    poll_seconds = min(
        min(rt.tm.cfg.poll_seconds, rt.rm.cfg.poll_seconds, rt.em_cfg.poll_seconds)
        for rt in runtimes
    )
    try:
        while True:
            cycle_start = time.monotonic()
            for rt in runtimes:
                run_once(rt)
            elapsed = time.monotonic() - cycle_start
            time.sleep(max(0.0, poll_seconds - elapsed))
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
