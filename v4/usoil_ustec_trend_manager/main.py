"""V4 USOIL/USTEC Trend Manager -- M30/M15 parents + M5 confirmation.
Run with: python -m v4.usoil_ustec_trend_manager.main

One process, one shared MT5 connection for both symbols, since they also
share ONE tv_scraper window (explicit user choice, 2026-08-30/31). Unlike
crypto_trend_manager, there is NO cross-symbol gating at all -- USOIL and
USTEC trade purely independently, each symbol's own entry logic and exit
management running in complete isolation from the other.

H1 is also on the shared scraper grid but is NOT used by this engine --
only M30/M15 (parents) and M5 (execution) participate; H1 is read but
reserved, unused for now (same status M1 has in crypto_trend_manager).

Entries are structure-based only (no ICT/OB-zone entries at all), same
post-removal design crypto_trend_manager already uses.
"""
from __future__ import annotations

import datetime
import time

import MetaTrader5 as mt5

from v4.usoil_ustec_trend_manager import broker
from v4.usoil_ustec_trend_manager.config import SYMBOLS, load_config
from v4.usoil_ustec_trend_manager.engine import EngineState, evaluate_symbol
from v4.usoil_ustec_trend_manager.exit_manager import ExitManagerState, evaluate_exit_actions

_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _log(msg: str) -> None:
    ts = datetime.datetime.now(tz=_IST).strftime("%H:%M:%S")
    print(f"[usoil_ustec_tm {ts} IST] {msg}")


def _comment(decision) -> str:
    return f"{decision.comment_tag}-{int(time.time())}"


def _fire(cfg, symbol: str, decision, reason: str, existing_direction) -> bool:
    """existing_direction: this symbol's REAL position direction before
    this poll's actions, or None if flat. This account is RETAIL_HEDGING
    -- an opposite-direction fire must explicitly close the old position
    first, same fix already confirmed live in crypto_trend_manager.

    Returns whether the caller should commit this M5 confirmation as
    "used" (state.mark_fired) -- added 2026-08-31, confirmed live: this
    used to be committed unconditionally in engine.py BEFORE the order
    was even attempted, so a broker-side rejection (retcode 10016
    "Invalid stops", a real USOIL buy) still burned the confirmation and
    left the account flat with no retry. Dry-run has no real fill to
    check, so it still counts as "used" (matches the old behavior, avoids
    reprinting the same signal every poll); live mode only returns True
    on a confirmed TRADE_RETCODE_DONE."""
    comment = _comment(decision)
    reversing = existing_direction is not None and existing_direction != decision.direction

    if not cfg.enable_trading:
        prefix = f"(would first CLOSE existing {existing_direction} position) " if reversing else ""
        _log(f"[{symbol}] ENTRY SIGNAL (DRY-RUN): {prefix}{decision.direction.upper()} | sl={decision.sl:.3f} | "
             f"{reason} | comment={comment!r}")
        return True

    if reversing:
        close_result = broker.close_position(cfg, symbol, f"V4S-REVERSE-CLOSE-{int(time.time())}")
        _log(f"[{symbol}] CLOSED existing {existing_direction} position before reversing -- result={close_result}")

    result = broker.send_market_order(cfg, symbol, decision.direction, decision.sl, comment)
    _log(f"[{symbol}] ORDER SENT: {decision.direction.upper()} lot={cfg.lot_sizes[symbol]} "
         f"sl={decision.sl:.3f} comment={comment!r} -- result={result}")
    ok = result is not None and result.retcode == 10009  # TRADE_RETCODE_DONE
    if not ok:
        _log(f"[{symbol}] order did NOT go through -- NOT marking this M5 confirmation as used, "
             f"will retry next poll")
    return ok


def _manage_open_position(cfg, exit_state: ExitManagerState, symbol: str) -> None:
    """Breakeven/tiered-booking/step-trailing for a currently open
    position -- runs independently of the entry logic, no TV data needed,
    just this position's own real numbers from MT5. No-op if nothing's
    open."""
    position = broker.get_position(cfg, symbol)
    if position is None:
        return

    entry_comment = position.comment  # captured BEFORE any action this poll touches it
    direction = "buy" if position.type == mt5.ORDER_TYPE_BUY else "sell"
    sl_update, closes = evaluate_exit_actions(
        exit_state, symbol, position.ticket, direction, position.price_open,
        position.price_current, position.sl,
    )

    current_sl = position.sl  # tracked so a same-poll comment-restore never reverts a same-poll SL move
    if sl_update is not None:
        current_sl = sl_update.new_sl
        if cfg.enable_trading:
            r = broker.modify_sl(cfg, symbol, position.ticket, sl_update.new_sl)
            _log(f"[{symbol}] EXIT MANAGER: SL -> {sl_update.new_sl:.3f} on ticket {position.ticket} -- result={r}")
        else:
            _log(f"[{symbol}] EXIT MANAGER (DRY-RUN): would move SL -> {sl_update.new_sl:.3f} "
                 f"on ticket {position.ticket}")

    for close in closes:
        # Same tradeoff/fix as crypto_trend_manager's own _manage_open_position:
        # partial-close deal comment carries the real tier reason, then an
        # immediate follow-up SLTP call restores the leftover position's
        # comment back to its original entry tag.
        comment = f"V4S-EXIT-{close.tier.upper()}-{int(time.time())}"
        if cfg.enable_trading:
            r = broker.partial_close(cfg, symbol, position.ticket, direction, close.volume, comment)
            _log(f"[{symbol}] EXIT MANAGER: partial close {close.tier} vol={close.volume} "
                 f"on ticket {position.ticket} -- result={r}")
            restore = broker.modify_sl(cfg, symbol, position.ticket, current_sl, comment=entry_comment)
            _log(f"[{symbol}] EXIT MANAGER: restored leftover position's comment to {entry_comment!r} "
                 f"on ticket {position.ticket} -- result={restore}")
        else:
            _log(f"[{symbol}] EXIT MANAGER (DRY-RUN): would partial close {close.tier} "
                 f"vol={close.volume} on ticket {position.ticket}, then restore its comment to {entry_comment!r}")


def run_once(cfg, state: EngineState, exit_state: ExitManagerState) -> None:
    for symbol in SYMBOLS:
        pos_dir = broker.position_direction(cfg, symbol)
        if pos_dir is not None:
            _manage_open_position(cfg, exit_state, symbol)

        # Live price fetched every poll now -- 2026-08-31, feeds engine.py's
        # SL-vs-live-price sanity check (see its own docstring). Transient
        # tick outages fail open (None) rather than skipping the whole
        # poll -- evaluate_symbol already tolerates current_price=None by
        # just not running that check, same as before this fix existed.
        try:
            current_price = broker.get_mid_price(symbol)
        except RuntimeError:
            current_price = None

        result = evaluate_symbol(state, symbol, pos_dir, current_price)
        _log(f"[{symbol}] {result.reason}")
        if result.decision is not None:
            fired_ok = _fire(cfg, symbol, result.decision, result.reason, pos_dir)
            if fired_ok:
                state.mark_fired(symbol, result.decision.confirm.event_time)


def main() -> None:
    cfg = load_config()
    broker.connect(cfg)
    state = EngineState(cfg.state_file)
    exit_state = ExitManagerState(cfg.exit_manager_state_file)

    mode = "LIVE (real orders will be sent)" if cfg.enable_trading else "DRY-RUN (signals printed only)"
    _log(f"connected -- watching {', '.join(SYMBOLS)} (M30/M15 parents, M5 confirmation), "
         f"polling every {cfg.poll_seconds}s -- {mode}")

    try:
        while True:
            run_once(cfg, state, exit_state)
            time.sleep(cfg.poll_seconds)
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
