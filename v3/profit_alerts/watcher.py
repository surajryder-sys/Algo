"""Profit-milestone alert loop -- see v3/profit_alerts/__init__.py for
what this is. Reads this system's own open MT5 positions (Trend
Manager's + Reversal Manager's magic numbers only) and fires one
Telegram message the first time each position's floating profit
reaches each configured points milestone -- read-only, never touches
an order.

Run with: python -m v3.profit_alerts.watcher
"""
from __future__ import annotations

import time

import MetaTrader5 as mt5

from v3.profit_alerts import mt5_positions
from v3.profit_alerts.config import Config, load_config
from v3.profit_alerts.profit_state import ProfitState
from v3.alert_manager.telegram_client import send_message


def _profit_points(position) -> float:
    """Floating profit in raw price-distance points -- direction-aware
    (BUY: price has to rise to profit; SELL: has to fall). Deliberately
    price-distance, not position.profit's own account-currency P&L
    (which also folds in lot size/contract value) -- matches the same
    "points" unit every other symbol-scaled distance in this repo uses
    (see entries.py's sl_buffer/market_max etc.), and the user's own
    milestone values (XAUUSD 12/25, BTCUSD 500/1000, ETHUSD 20/40) only
    make sense as price points, not dollars."""
    if position.type == mt5.POSITION_TYPE_BUY:
        return position.price_current - position.price_open
    return position.price_open - position.price_current


def _format_alert(position, milestone: float, profit_points: float) -> str:
    direction_label = "BUY" if position.type == mt5.POSITION_TYPE_BUY else "SELL"
    return (
        f"\U0001F4B0 {position.symbol} {direction_label} +{milestone:g} points\n"
        f"Entry: {position.price_open:.2f}  Current: {position.price_current:.2f}\n"
        f"Profit: {profit_points:.2f} points\n"
        f"Ticket: {position.ticket}"
    )


def run_once(cfg: Config, state: ProfitState) -> None:
    open_tickets = set()

    for sym_cfg in cfg.symbols:
        try:
            positions = mt5_positions.get_positions(sym_cfg.symbol, cfg.magic_numbers)
        except Exception as exc:
            print(f"[profit_alerts] {sym_cfg.symbol} position ERROR: {exc}")
            continue

        for position in positions:
            open_tickets.add(position.ticket)
            profit_points = _profit_points(position)

            for milestone in sym_cfg.milestones:
                if profit_points < milestone:
                    continue
                if state.already_alerted(position.ticket, milestone):
                    continue

                text = _format_alert(position, milestone, profit_points)
                try:
                    send_message(cfg.telegram_bot_token, cfg.telegram_chat_id, text)
                    state.mark_alerted(position.ticket, milestone)
                    print(f"[profit_alerts] sent alert: {sym_cfg.symbol} ticket={position.ticket} "
                          f"milestone={milestone:g} profit_points={profit_points:.2f}")
                except Exception as exc:
                    # Deliberately NOT marked alerted on a failed send --
                    # matches every other bot's own retry-next-cycle
                    # pattern in this repo rather than silently losing it.
                    print(f"[profit_alerts] Telegram send ERROR: {exc}")

    state.prune(open_tickets)


def main() -> None:
    cfg = load_config()
    if not cfg.telegram_bot_token or not cfg.telegram_chat_id:
        raise RuntimeError("PROFIT_ALERTS_TELEGRAM_BOT_TOKEN / PROFIT_ALERTS_TELEGRAM_CHAT_ID must be set in .env")

    mt5_positions.connect(cfg)
    state = ProfitState(cfg.state_file)

    print(f"[profit_alerts] watching {[s.symbol for s in cfg.symbols]} "
          f"(magic numbers {cfg.magic_numbers}), polling every {cfg.poll_seconds}s")
    try:
        while True:
            try:
                run_once(cfg, state)
            except Exception as exc:
                print(f"[profit_alerts] ERROR: {exc}")
            time.sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        mt5_positions.shutdown()


if __name__ == "__main__":
    main()
