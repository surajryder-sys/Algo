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

Exit/profit-booking (breakeven, partial closes) is explicitly OUT of
scope here too -- entry + initial SL only, per 2026-08-29 "entry only for
now"; a follow-up Trade/Exit Manager is still to come.
"""
from __future__ import annotations

import datetime
import time

from v4.crypto_trend_manager import broker
from v4.crypto_trend_manager.config import PRIMARY_SYMBOL, SYMBOLS, load_config
from v4.crypto_trend_manager.engine import EngineState, evaluate_symbol

_SECONDARY_SYMBOL = next(s for s in SYMBOLS if s != PRIMARY_SYMBOL)
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _log(msg: str) -> None:
    ts = datetime.datetime.now(tz=_IST).strftime("%H:%M:%S")
    print(f"[crypto_tm {ts} IST] {msg}")


def _comment(decision) -> str:
    return f"{decision.comment_tag}-{int(time.time())}"


def _fire(cfg, symbol: str, decision, reason: str) -> None:
    comment = _comment(decision)
    if not cfg.enable_trading:
        _log(f"[{symbol}] ENTRY SIGNAL (DRY-RUN): {decision.direction.upper()} | sl={decision.sl:.2f} | "
             f"{reason} | comment={comment!r}")
        return
    result = broker.send_market_order(cfg, symbol, decision.direction, decision.sl, comment)
    _log(f"[{symbol}] ORDER SENT: {decision.direction.upper()} lot={cfg.lot_sizes[symbol]} "
         f"sl={decision.sl:.2f} comment={comment!r} -- result={result}")


def run_once(cfg, state: EngineState) -> None:
    btc_pos_dir = broker.position_direction(cfg, PRIMARY_SYMBOL)
    eth_pos_dir = broker.position_direction(cfg, _SECONDARY_SYMBOL)

    btc_result = evaluate_symbol(state, PRIMARY_SYMBOL)
    _log(f"[{PRIMARY_SYMBOL}] {btc_result.reason}")
    if btc_result.decision is not None:
        _fire(cfg, PRIMARY_SYMBOL, btc_result.decision, btc_result.reason)

    # Most current view of BTCUSD's direction for this poll's gating -- use
    # the fresh decision if it just fired (more responsive than waiting a
    # full poll cycle for MT5 to reflect the new position), else whatever
    # was already open at the start of this poll.
    effective_btc_dir = btc_result.decision.direction if btc_result.decision is not None else btc_pos_dir

    eth_result = evaluate_symbol(state, _SECONDARY_SYMBOL)
    if eth_result.decision is not None and effective_btc_dir is not None \
            and eth_result.decision.direction != effective_btc_dir:
        _log(f"[{_SECONDARY_SYMBOL}] {eth_result.reason} -- BLOCKED: BTCUSD is currently {effective_btc_dir}, "
             f"ETHUSD keeps quiet on the opposite direction")
    else:
        _log(f"[{_SECONDARY_SYMBOL}] {eth_result.reason}")
        if eth_result.decision is not None:
            _fire(cfg, _SECONDARY_SYMBOL, eth_result.decision, eth_result.reason)

    if effective_btc_dir is not None and eth_pos_dir is not None and eth_pos_dir != effective_btc_dir:
        comment = f"CTM-FOLLOW-CLOSE-{int(time.time())}"
        if not cfg.enable_trading:
            _log(f"[{_SECONDARY_SYMBOL}] WOULD CLOSE (DRY-RUN): currently {eth_pos_dir} while BTCUSD is "
                 f"{effective_btc_dir} -- ETHUSD must follow BTCUSD's bias")
        else:
            result = broker.close_position(cfg, _SECONDARY_SYMBOL, comment)
            _log(f"[{_SECONDARY_SYMBOL}] CLOSED (follows BTCUSD): was {eth_pos_dir} while BTCUSD is "
                 f"{effective_btc_dir} -- result={result}")


def main() -> None:
    cfg = load_config()
    broker.connect(cfg)
    state = EngineState(cfg.state_file)

    mode = "LIVE (real orders will be sent)" if cfg.enable_trading else "DRY-RUN (signals printed only)"
    _log(f"connected -- watching {', '.join(SYMBOLS)} (M30/M15 parents, M5 confirmation), "
         f"polling every {cfg.poll_seconds}s -- {mode}")

    try:
        while True:
            run_once(cfg, state)
            time.sleep(cfg.poll_seconds)
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
