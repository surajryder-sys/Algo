"""V4 crypto Trend Manager -- BTCUSD + ETHUSD, M30/M15 parents + M5
confirmation. Run with: python -m v4.crypto_trend_manager.main

One process, one shared MT5 connection for both symbols (not two
independent processes like XAUUSD's trend_manager) -- required for the
cross-symbol gating below, which needs same-poll visibility into both
symbols' state at once, not two separately-timed engines guessing at each
other's position through a file.

Per-symbol entry logic (engine.py) is fully independent and identical for
both symbols ("executions are purely based on individual instruments") --
this module's only extra responsibility is BTCUSD-primary/ETHUSD-follows
gating, applied AFTER each symbol's own engine has already decided
independently, never inside it:
  1. ETHUSD keeps quiet on any confirmed setup that OPPOSES BTCUSD's
     current position direction (BTCUSD flat/indecisive imposes no
     restriction either way).
  2. Whenever BTCUSD holds a position and ETHUSD is currently holding the
     OPPOSITE direction, ETHUSD's opposing position is closed proactively
     -- independent of whatever ETHUSD's own engine says this poll --
     then ETHUSD sits flat waiting for its own fresh, independently
     confirmed setup.

A blocked ETHUSD candidate is NOT retried later even if BTCUSD's bias
changes afterward -- engine.py already marks it fired the moment M5
confirms it, gate or no gate (explicit design choice: re-firing a stale
confirmation against a since-moved market is worse than requiring a
genuinely fresh candidate).

M1 is explicitly NOT part of this engine -- both charts already carry an
M1 ATR-only pane (see [[project_tv_scraper_multi_symbol_setup]]) and
v4/atr_flip_race races it, but that's reserved for a future Reversal
Manager, unbuilt and unwired here ("M1 we have added for reversal
manager, nothing doing as of now with it").

Exit/profit-booking (exit_manager.py, added 2026-08-29) runs independently
of the entry logic above, per symbol, for whichever position is currently
open -- breakeven + tiered partial-booking + a continuously trailing SL,
own thresholds per symbol (see that module's own docstring). Applies
identically to BOTH symbols regardless of which one (primary/secondary)
opened the trade, or whether it fired via STR or ICT.
"""
from __future__ import annotations

import datetime
import time

import MetaTrader5 as mt5

from v4.crypto_trend_manager import broker
from v4.crypto_trend_manager.config import PRIMARY_SYMBOL, SYMBOLS, load_config
from v4.crypto_trend_manager.engine import EngineState, evaluate_symbol
from v4.crypto_trend_manager.exit_manager import ExitManagerState, evaluate_exit_actions

_SECONDARY_SYMBOL = next(s for s in SYMBOLS if s != PRIMARY_SYMBOL)
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _log(msg: str) -> None:
    ts = datetime.datetime.now(tz=_IST).strftime("%H:%M:%S")
    print(f"[crypto_tm {ts} IST] {msg}")


def _comment(decision) -> str:
    return f"{decision.comment_tag}-{int(time.time())}"


def _fire(cfg, symbol: str, decision, reason: str, existing_direction) -> None:
    """existing_direction: this symbol's REAL position direction before
    this poll's actions, or None if flat. Confirmed live bug, 2026-08-29:
    this account is RETAIL_HEDGING, not netting -- MT5 will NOT
    automatically close/reverse an existing position just because an
    opposite-direction order comes in; it opens a second, separate hedged
    position instead. So an opposite-direction fire must explicitly close
    the old position here FIRST, replicating netting manually -- same
    fix applied to V4/XAUUSD's broker, which had the identical unverified
    assumption."""
    comment = _comment(decision)
    reversing = existing_direction is not None and existing_direction != decision.direction

    if not cfg.enable_trading:
        prefix = f"(would first CLOSE existing {existing_direction} position) " if reversing else ""
        _log(f"[{symbol}] ENTRY SIGNAL (DRY-RUN): {prefix}{decision.direction.upper()} | sl={decision.sl:.2f} | "
             f"{reason} | comment={comment!r}")
        return

    if reversing:
        close_result = broker.close_position(cfg, symbol, f"V4S-REVERSE-CLOSE-{int(time.time())}")
        _log(f"[{symbol}] CLOSED existing {existing_direction} position before reversing -- result={close_result}")

    result = broker.send_market_order(cfg, symbol, decision.direction, decision.sl, comment)
    _log(f"[{symbol}] ORDER SENT: {decision.direction.upper()} lot={cfg.lot_sizes[symbol]} "
         f"sl={decision.sl:.2f} comment={comment!r} -- result={result}")


def _manage_open_position(cfg, exit_state: ExitManagerState, symbol: str) -> None:
    """Breakeven/tiered-booking/step-trailing for a currently open crypto
    Trend Manager position, per the user's explicit thresholds 2026-08-29
    -- runs independently of the entry logic, no TV data needed, just this
    position's own real numbers from MT5. No-op if nothing's open."""
    position = broker.get_position(cfg, symbol)
    if position is None:
        return

    direction = "buy" if position.type == mt5.ORDER_TYPE_BUY else "sell"
    sl_update, closes = evaluate_exit_actions(
        exit_state, symbol, position.ticket, direction, position.price_open,
        position.price_current, position.sl,
    )

    if sl_update is not None:
        if cfg.enable_trading:
            r = broker.modify_sl(cfg, symbol, position.ticket, sl_update.new_sl)
            _log(f"[{symbol}] EXIT MANAGER: SL -> {sl_update.new_sl:.2f} on ticket {position.ticket} -- result={r}")
        else:
            _log(f"[{symbol}] EXIT MANAGER (DRY-RUN): would move SL -> {sl_update.new_sl:.2f} "
                 f"on ticket {position.ticket}")

    for close in closes:
        comment = f"V4S-EXIT-{close.tier.upper()}-{int(time.time())}"
        if cfg.enable_trading:
            r = broker.partial_close(cfg, symbol, position.ticket, direction, close.volume, comment)
            _log(f"[{symbol}] EXIT MANAGER: partial close {close.tier} vol={close.volume} "
                 f"on ticket {position.ticket} -- result={r}")
        else:
            _log(f"[{symbol}] EXIT MANAGER (DRY-RUN): would partial close {close.tier} "
                 f"vol={close.volume} on ticket {position.ticket}")


def run_once(cfg, state: EngineState, exit_state: ExitManagerState) -> None:
    btc_pos_dir = broker.position_direction(cfg, PRIMARY_SYMBOL)
    eth_pos_dir = broker.position_direction(cfg, _SECONDARY_SYMBOL)

    if btc_pos_dir is not None:
        _manage_open_position(cfg, exit_state, PRIMARY_SYMBOL)
    if eth_pos_dir is not None:
        _manage_open_position(cfg, exit_state, _SECONDARY_SYMBOL)

    btc_result = evaluate_symbol(state, PRIMARY_SYMBOL, btc_pos_dir)
    _log(f"[{PRIMARY_SYMBOL}] {btc_result.reason}")
    if btc_result.decision is not None:
        _fire(cfg, PRIMARY_SYMBOL, btc_result.decision, btc_result.reason, btc_pos_dir)

    # Most current view of each symbol's direction for this poll's gating
    # -- use the fresh decision if it just fired (more responsive than
    # waiting a full poll cycle for MT5 to reflect the new position), else
    # whatever was already open at the start of this poll. Confirmed live
    # bug, 2026-08-29: using the STALE pre-poll eth_pos_dir for the
    # follow-close check below (instead of tracking ETHUSD's own
    # just-fired reversal here) could wrongly re-close a position ETHUSD's
    # own engine had correctly just reversed into agreement with BTCUSD
    # this same poll.
    effective_btc_dir = btc_result.decision.direction if btc_result.decision is not None else btc_pos_dir

    eth_result = evaluate_symbol(state, _SECONDARY_SYMBOL, eth_pos_dir)
    eth_fired_direction = None
    if eth_result.decision is not None and effective_btc_dir is not None \
            and eth_result.decision.direction != effective_btc_dir:
        _log(f"[{_SECONDARY_SYMBOL}] {eth_result.reason} -- BLOCKED: BTCUSD is currently {effective_btc_dir}, "
             f"ETHUSD keeps quiet on the opposite direction")
    else:
        _log(f"[{_SECONDARY_SYMBOL}] {eth_result.reason}")
        if eth_result.decision is not None:
            _fire(cfg, _SECONDARY_SYMBOL, eth_result.decision, eth_result.reason, eth_pos_dir)
            eth_fired_direction = eth_result.decision.direction

    effective_eth_dir = eth_fired_direction if eth_fired_direction is not None else eth_pos_dir

    if effective_btc_dir is not None and effective_eth_dir is not None and effective_eth_dir != effective_btc_dir:
        comment = f"V4S-FOLLOW-CLOSE-{int(time.time())}"
        if not cfg.enable_trading:
            _log(f"[{_SECONDARY_SYMBOL}] WOULD CLOSE (DRY-RUN): currently {effective_eth_dir} while BTCUSD is "
                 f"{effective_btc_dir} -- ETHUSD must follow BTCUSD's bias")
        else:
            result = broker.close_position(cfg, _SECONDARY_SYMBOL, comment)
            _log(f"[{_SECONDARY_SYMBOL}] CLOSED (follows BTCUSD): was {effective_eth_dir} while BTCUSD is "
                 f"{effective_btc_dir} -- result={result}")


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
