"""V6-Sentinel Scalper -- main loop. Built 2026-09-22 (user's own rule, see
scalper_config.py's own docstring for the exact words). One runtime bundle
PER SYMBOL in config.ACTIVE_SYMBOLS, same multi-instrument shape as
trend_main.py/reversal_main.py.

Run with: python -m v6_sentinel.scalper_main

THE RULE, in full:
  - ENTRY: the same hammer/star + HTF-line-or-virgin-OB-zone touch signal
    EA-CandleExit uses to CLOSE trades, reused here to OPEN one instead --
    see candle_touch.py's own docstring for the complete pattern/pairing/
    scope/touch rule, and scalper_entry.py's own docstring for how it's
    applied to entries specifically (SL/TP calculation, one-trade-per-
    pattern-event eligibility).
  - LOTS: 0.05 fixed (scalper_config.py).
  - SL: the pattern candle's own low (BUY) / high (SELL), +/- a 2.0-point
    buffer.
  - TP: a broker-side TP at a fixed 1:1 R:R from the SL distance -- the
    ONLY V6S manager that ever places one (see broker.send_market_order's
    own tp= docstring). Recorded in scalper_own_tp.ScalperOwnTPStore on
    every fill so Exit Manager can tell "Scalper's own untouched TP" apart
    from "the user has since changed it" (broker.is_paused_by_manual_tp).
  - EXIT: no independent exit logic lives here at all -- a Scalper trade
    closes via ITS OWN broker-side TP hitting, its own SL hitting, OR any
    Exit Manager component's own close (bias flip / LTF touch / CandleExit
    STR or ICT), exactly like every other manager's positions -- Scalper
    is simply a 4th WatchedSource in exit_manager_config.py now. This file
    never itself closes a position.

Safety: V6S_SCALPER_{SYMBOL}_ENABLE_TRADING must be explicitly true for any
order to actually be sent; left unset (default false) every decision is
printed and logged but nothing touches the account.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from v6_sentinel import broker, config, decision_log, heartbeat, scalper_entry, trade_journal
from v6_sentinel.bridge_bar_flip import BridgeBarFlipTracker
from v6_sentinel.nlb_nsb_block import BlockStore
from v6_sentinel.reversal_config import load_symbol_config as load_rm_config
from v6_sentinel.scalper_config import ScalperSymbolConfig, load_symbol_config
from v6_sentinel.scalper_own_tp import ScalperOwnTPStore

_DIR_LABEL = {1: "BUY", -1: "SELL"}


def _entry_comment(sig: scalper_entry.ScalperSignal) -> str:
    """e.g. "V6S-SC-STR-H" (hammer via an HTF line) or "V6S-SC-ICT-S" (star
    via an OB zone) -- well under MT5's 31-character comment limit."""
    pattern_letter = "H" if sig.pattern_name == "hammer" else "S"
    return f"V6S-SC-{sig.sub_tag}-{pattern_letter}"


@dataclass
class _SymbolRuntime:
    cfg: ScalperSymbolConfig
    tracker: BridgeBarFlipTracker
    eligibility: scalper_entry.ScalperEligibilityStore
    own_tp: ScalperOwnTPStore
    block_state_file: str                 # RM-ICT's own Block -- read-only, same source EA-CandleExit uses
    journal: trade_journal.TradeJournal


def _build_runtime(symbol: str) -> _SymbolRuntime:
    cfg = load_symbol_config(symbol)
    return _SymbolRuntime(
        cfg=cfg,
        tracker=BridgeBarFlipTracker(cfg.bridge_bar_flip_state_file),
        eligibility=scalper_entry.ScalperEligibilityStore(cfg.eligibility_state_file),
        own_tp=ScalperOwnTPStore(cfg.own_tp_state_file),
        block_state_file=load_rm_config(symbol).nlb_nsb_block_state_file,
        journal=trade_journal.TradeJournal(cfg.trade_journal_file, "SCALPER", symbol),
    )


def _open_position(cfg: ScalperSymbolConfig, sig: scalper_entry.ScalperSignal, journal: trade_journal.TradeJournal,
                   own_tp: ScalperOwnTPStore) -> bool:
    """True if the entry went through (filled, or enable_trading is False
    so it's decision-only) -- False only on a genuine order rejection,
    same "caller must not mark the event handled on False" contract
    trend_main._open_position() uses."""
    comment = _entry_comment(sig)
    print(f"[V6S-SC-ENTRY] {cfg.symbol} {_DIR_LABEL[sig.direction]} (M{sig.pattern_tf} {sig.pattern_name} / "
          f"{sig.sub_tag} {sig.trigger_label}) sl={sig.sl:.3f} tp={sig.tp:.3f}")
    detail = {"rule": "hammer/star + touch (Scalper)", "pattern": sig.pattern_name, "sub_source": sig.sub_tag,
              "pattern_timeframe": sig.pattern_tf, "trigger": sig.trigger_label, "bar_time": sig.bar_time,
              "sl": sig.sl, "tp": sig.tp}
    if not cfg.enable_trading:
        print("[V6S-SC-ENTRY] enable_trading is false -- decision only, no order sent")
        decision_log.log(cfg.decision_log_file, "entry_decision_only", direction=_DIR_LABEL[sig.direction], **detail)
        return True
    result = broker.send_market_order(cfg.symbol, sig.direction, cfg.lots, sig.sl, cfg.magic_number,
                                      cfg.deviation_points, comment, tp=sig.tp)
    if not result.ok:
        print(f"[V6S-SC-ENTRY] order_send failed: retcode={result.retcode} comment={result.comment}")
        decision_log.log(cfg.decision_log_file, "entry_failed", direction=_DIR_LABEL[sig.direction],
                         retcode=result.retcode, broker_comment=result.comment, **detail)
        return False
    print(f"[V6S-SC-ENTRY] filled, ticket={result.ticket}")
    decision_log.log(cfg.decision_log_file, "entry_filled", direction=_DIR_LABEL[sig.direction],
                     ticket=result.ticket, **detail)
    if result.ticket is not None:
        journal.entry(result.ticket, _DIR_LABEL[sig.direction], cfg.lots, sig.sl, comment, detail)
        own_tp.set(result.ticket, sig.tp)
    return True


def run_once(rt: _SymbolRuntime) -> None:
    cfg = rt.cfg
    bid, ask = broker.get_tick_price(cfg.symbol)
    block = BlockStore(rt.block_state_file)   # read-only, RM-ICT's own Block, never written to here

    sig = scalper_entry.find_signal(cfg.symbol, rt.tracker, block, rt.eligibility, cfg.sl_buffer, cfg.risk_reward,
                                    bid, ask)
    if sig is None:
        return
    if _open_position(cfg, sig, rt.journal, rt.own_tp):
        rt.eligibility.mark_traded(sig.pattern_tf, sig.pattern_name, sig.bar_time)

    open_tickets = [p.ticket for p in broker.get_positions(cfg.symbol, cfg.magic_number)]
    rt.journal.reconcile(open_tickets)


def main() -> None:
    runtimes = [_build_runtime(symbol) for symbol in config.ACTIVE_SYMBOLS]
    for rt in runtimes:
        print(f"[V6S-SC] {rt.cfg.symbol} starting -- magic={rt.cfg.magic_number} lots={rt.cfg.lots} "
              f"sl_buffer={rt.cfg.sl_buffer} risk_reward={rt.cfg.risk_reward} enable_trading={rt.cfg.enable_trading} "
              f"poll={rt.cfg.poll_seconds}s")
        broker.connect(rt.cfg.symbol, rt.cfg.mt5_terminal_path, rt.cfg.mt5_login, rt.cfg.mt5_password,
                       rt.cfg.mt5_server)
    try:
        while True:
            for rt in runtimes:
                try:
                    run_once(rt)
                except Exception as exc:  # noqa: BLE001 -- keep the loop alive, log and continue
                    print(f"[V6S-SC] {rt.cfg.symbol} cycle error: {exc!r}")
                heartbeat.write(rt.cfg.heartbeat_file)
            time.sleep(min(rt.cfg.poll_seconds for rt in runtimes))
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
