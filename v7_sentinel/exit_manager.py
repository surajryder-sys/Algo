"""V7-Sentinel Exit Manager -- a standalone process that watches every open position on the
account (TM-STR, RM-ICT) and closes trades opposite to a real signal, independent of
which manager opened them. Rebuilt from scratch 2026-09-21 (user: "lets re build the exit
manager from the beginning"), replacing the earlier entry-watching / timeframe-hierarchy
design entirely. Scalper and its two dedicated components (EA-CandleExit, the Scalper-only
M3/M5 CISD exit) were removed entirely 2026-09-25 along with Scalper itself. LTF Exit Manager
(the touch + M1-flip/M3-CISD component) was removed entirely 2026-09-25 too, on request --
along with it went reversal_entry.py (its only remaining function, scan_touches(), had no
other caller left once RM-STR's own entry engine was stripped out of that file) and
htf_levels.py (the D1-M3 ATR+Supertrend level store, which had no consumer left besides
reversal_entry.py and this component). Bias Exit Manager (exit_manager_bias.py -- a fresh
M5/M15 CISD alone, with no touch of any kind, closing every open position on the opposite
side) was removed entirely 2026-09-25 too, on request (user: "cisd is only for entry, we are
taking out of exit") -- CISD now only ever confirms a genuine zone/level TOUCH (ICT Exit's
own role for it), never stands alone as a close signal by itself.

ONE COMPONENT LEFT (user's own split, 2026-09-24):
  ICT EXIT (exit_manager_ict.py, built 2026-09-24) -- an OB zone (M3/M5 from the merged
  TV+MT5 store, M15+ from RM-ICT's own TV-only Block) touch, opposite to the position's own
  direction, followed by a matching-direction CISD confirmation closes it: M1 or M3 (with a
  direct-fire exception) for M3/M5-timeframe zones, M3-only for anything larger. Applies to
  every manager, no exemptions. See that module's own docstring for the exact rule.

Also skips a position that currently carries a manual TP
(broker.has_manual_tp() / is_paused_by_manual_tp()).

ALSO RUNS (alert-only, sends no orders, not gated by enable_trading): zone_touch_alert.py --
Telegram alert whenever price touches a virgin OB zone while at least one position is open,
once per minute per zone. See that module's own docstring for the exact rule.

Run with: python -m v7_sentinel.exit_manager

Safety: V7S_EM_{SYMBOL}_ENABLE_TRADING must be explicitly true for any component to send a
real close; left unset (default false) every decision is printed and logged but nothing
touches the account.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from v7_sentinel import broker, config, exit_manager_ict, heartbeat, zone_touch_alert
from v7_sentinel.exit_manager_config import ExitManagerSymbolConfig, load_symbol_config


@dataclass
class _SymbolRuntime:
    cfg: ExitManagerSymbolConfig
    ict: exit_manager_ict.ICTExitRuntime
    zone_alert: zone_touch_alert.ZoneTouchAlertRuntime


def run_once(rt: _SymbolRuntime) -> None:
    exit_manager_ict.run_once(rt.cfg, rt.ict)                    # component: ICT Exit
    zone_touch_alert.run_once(rt.cfg, rt.zone_alert)                # alert-only: Zone Touch Alert


def main() -> None:
    cfgs = [load_symbol_config(symbol) for symbol in config.ACTIVE_SYMBOLS]
    runtimes = [_SymbolRuntime(cfg=c, ict=exit_manager_ict.build_runtime(c),
                               zone_alert=zone_touch_alert.build_runtime()) for c in cfgs]
    for c in cfgs:
        alert_status = "on" if (c.alerts_bot_token and c.alerts_chat_id) else "off (unconfigured)"
        print(f"[V7S-XM] {c.symbol} starting -- enable_trading={c.enable_trading} poll={c.poll_seconds}s "
              f"watching={[s.name for s in c.sources]} components=[ict] "
              f"zone_touch_alert={alert_status}")
        broker.connect(c.symbol, c.mt5_terminal_path, c.mt5_login, c.mt5_password, c.mt5_server)
    try:
        while True:
            for rt in runtimes:
                try:
                    run_once(rt)
                except Exception as exc:  # noqa: BLE001 -- keep the loop alive, log and continue
                    print(f"[V7S-XM] {rt.cfg.symbol} cycle error: {exc!r}")
                heartbeat.write(rt.cfg.heartbeat_file)
            time.sleep(min(c.poll_seconds for c in cfgs))
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
