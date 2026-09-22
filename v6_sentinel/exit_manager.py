"""V6-Sentinel Exit Manager -- a standalone process that watches every open position on the
account (TM-STR, RM-STR, RM-ICT, SCALPER) and closes trades opposite to a real signal, independent of
which manager opened them. Rebuilt from scratch 2026-09-21 (user: "lets re build the exit
manager from the beginning"), replacing the earlier entry-watching / timeframe-hierarchy
design entirely.

THREE COMPONENTS (user's own split, 2026-09-21/22):
  1. BIAS EXIT MANAGER (exit_manager_bias.py, built) -- a fresh M5 or M15 CISD closes every
     open position on the opposite side, across every manager, as long as that CISD's candle
     closed AFTER the position opened (an already-existing CISD never closes a trade). See
     exit_manager_bias.py's own docstring for the exact rule.
  2. LTF EXIT MANAGER (exit_manager_ltf.py, built 2026-09-22) -- a touch of any D1-M15
     support/resistance level followed by a FRESH, matching-direction M1 CISD closes every
     open position on the opposite side, across every manager. See exit_manager_ltf.py's own
     docstring for the exact rule and worked example.
  3. EA-CANDLEEXIT (exit_manager_candle.py, built 2026-09-22) -- a hammer on M3/M5 that
     touches an HTF support level (same candle) closes SELL positions; a star on M3/M5 that
     touches an HTF resistance level (same candle) closes BUY positions. See
     exit_manager_candle.py's own docstring for the exact rule.

All three additionally skip a position that currently carries a manual TP
(broker.has_manual_tp()), across every manager, no exemptions.

ALSO RUNS (alert-only, sends no orders, not gated by enable_trading): zone_touch_alert.py --
Telegram alert whenever price touches a virgin OB zone while at least one position is open,
once per minute per zone. See that module's own docstring for the exact rule.

Run with: python -m v6_sentinel.exit_manager

Safety: V6S_EM_{SYMBOL}_ENABLE_TRADING must be explicitly true for any component to send a
real close; left unset (default false) every decision is printed and logged but nothing
touches the account.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from v6_sentinel import broker, config, exit_manager_bias, exit_manager_candle, exit_manager_ltf, heartbeat, \
    zone_touch_alert
from v6_sentinel.exit_manager_config import ExitManagerSymbolConfig, load_symbol_config


@dataclass
class _SymbolRuntime:
    cfg: ExitManagerSymbolConfig
    ltf: exit_manager_ltf.LTFExitRuntime
    candle: exit_manager_candle.CandleExitRuntime
    zone_alert: zone_touch_alert.ZoneTouchAlertRuntime


def run_once(rt: _SymbolRuntime) -> None:
    exit_manager_bias.run_once(rt.cfg)                        # component 1: Bias Exit Manager
    exit_manager_ltf.run_once(rt.cfg, rt.ltf)                   # component 2: LTF Exit Manager
    exit_manager_candle.run_once(rt.cfg, rt.candle)               # component 3: EA-CandleExit
    zone_touch_alert.run_once(rt.cfg, rt.zone_alert)                # alert-only: Zone Touch Alert


def main() -> None:
    cfgs = [load_symbol_config(symbol) for symbol in config.ACTIVE_SYMBOLS]
    runtimes = [_SymbolRuntime(cfg=c, ltf=exit_manager_ltf.build_runtime(c),
                               candle=exit_manager_candle.build_runtime(c),
                               zone_alert=zone_touch_alert.build_runtime()) for c in cfgs]
    for c in cfgs:
        alert_status = "on" if (c.alerts_bot_token and c.alerts_chat_id) else "off (unconfigured)"
        print(f"[V6S-XM] {c.symbol} starting -- enable_trading={c.enable_trading} poll={c.poll_seconds}s "
              f"watching={[s.name for s in c.sources]} components=[bias, ltf, candle] zone_touch_alert={alert_status}")
        broker.connect(c.symbol, c.mt5_terminal_path, c.mt5_login, c.mt5_password, c.mt5_server)
    try:
        while True:
            for rt in runtimes:
                try:
                    run_once(rt)
                except Exception as exc:  # noqa: BLE001 -- keep the loop alive, log and continue
                    print(f"[V6S-XM] {rt.cfg.symbol} cycle error: {exc!r}")
                heartbeat.write(rt.cfg.heartbeat_file)
            time.sleep(min(c.poll_seconds for c in cfgs))
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
