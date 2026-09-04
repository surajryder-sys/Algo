"""Profit-milestone alert loop for V5-Sentinel -- reads V5-Sentinel's
own open MT5 positions (V5S_MAGIC_NUMBER) and fires one Telegram
message, to every APPROVED subscriber (see profit_alerts_subscribers.py),
the first time each position's floating profit reaches each milestone
in an OPEN-ENDED ladder: 10, 15, 20, 25, 30, ... (step 5 throughout,
including the 10->15 leg) -- user's explicit rule 2026-09-04, "one alert
at 10 points gain, then again at 15 from entry and subsequent each 5
points until the trade gets closed." Continues indefinitely, no upper
bound -- see _milestones_up_to. Read-only -- never touches an order.

Run alongside profit_alerts_listener.py (separate process, handles
subscriber approval commands -- see that module's own docstring for why
they must be separate).

Run with: python -m v5_sentinel.profit_alerts_watcher
"""
from __future__ import annotations

import time

import MetaTrader5 as mt5

from v5_sentinel import profit_alerts_mt5 as mt5_positions
from v5_sentinel.profit_alerts_config import Config, load_config
from v5_sentinel.profit_alerts_state import ProfitState
from v5_sentinel.profit_alerts_subscribers import SubscriberStore
from v5_sentinel.profit_alerts_telegram import send_message


def _profit_points(position) -> float:
    if position.type == mt5.POSITION_TYPE_BUY:
        return position.price_current - position.price_open
    return position.price_open - position.price_current


def _milestones_up_to(profit_points: float, start: float, step: float) -> list[float]:
    """Expands the open-ended ladder (10, 15, 20, 25, ... -- a single
    uniform step-5 sequence starting at 10, see Config.milestone_start/
    step's own docstring) on demand, up to and including whatever
    milestone the CURRENT profit has actually reached. Cheap even after
    a huge favorable move -- this is a handful of iterations, not
    thousands, for any realistic XAUUSD point count."""
    milestones = []
    m = start
    while m <= profit_points:
        milestones.append(m)
        m += step
    return milestones


def _format_alert(position, milestone: float, profit_points: float) -> str:
    direction_label = "BUY" if position.type == mt5.POSITION_TYPE_BUY else "SELL"
    return (
        f"\U0001F4B0 {position.symbol} {direction_label} +{milestone:g} points\n"
        f"Entry: {position.price_open:.2f}  Current: {position.price_current:.2f}\n"
        f"Profit: {profit_points:.2f} points\n"
        f"Ticket: {position.ticket}"
    )


def run_once(cfg: Config, state: ProfitState, subscribers: SubscriberStore) -> None:
    try:
        positions = mt5_positions.get_positions(cfg.symbol, cfg.magic_number)
    except Exception as exc:
        print(f"[v5_sentinel.profit_alerts] {cfg.symbol} position ERROR: {exc}")
        return

    open_tickets = set()
    for position in positions:
        open_tickets.add(position.ticket)
        profit_points = _profit_points(position)

        for milestone in _milestones_up_to(profit_points, cfg.milestone_start, cfg.milestone_step):
            if state.already_alerted(position.ticket, milestone):
                continue

            text = _format_alert(position, milestone, profit_points)
            recipients = subscribers.approved_chat_ids()
            all_ok = True
            for chat_id in recipients:
                try:
                    send_message(cfg.telegram_bot_token, chat_id, text)
                except Exception as exc:
                    all_ok = False
                    print(f"[v5_sentinel.profit_alerts] Telegram send ERROR (chat_id={chat_id}): {exc}")
            if all_ok:
                # Marked alerted only once ALL current subscribers got
                # it -- a partial-send failure retries the WHOLE
                # milestone next cycle (including resending to whoever
                # already got it), matching the existing "don't lose an
                # alert to a transient send failure" convention rather
                # than risk silently skipping someone.
                state.mark_alerted(position.ticket, milestone)
                print(f"[v5_sentinel.profit_alerts] sent alert: {cfg.symbol} ticket={position.ticket} "
                      f"milestone={milestone:g} profit_points={profit_points:.2f} "
                      f"recipients={len(recipients)}")

    state.prune(open_tickets)


def main() -> None:
    cfg = load_config()
    if not cfg.telegram_bot_token or not cfg.owner_chat_id:
        raise RuntimeError("PROFIT_ALERTS_TELEGRAM_BOT_TOKEN / PROFIT_ALERTS_TELEGRAM_CHAT_ID must be set in .env")

    mt5_positions.connect(cfg)
    state = ProfitState(cfg.state_file)
    subscribers = SubscriberStore(cfg.subscribers_file, cfg.owner_chat_id)

    print(f"[v5_sentinel.profit_alerts] watching {cfg.symbol} (magic {cfg.magic_number}), "
          f"polling every {cfg.poll_seconds}s, {len(subscribers.approved_chat_ids())} approved subscriber(s)")
    try:
        while True:
            try:
                run_once(cfg, state, subscribers)
            except Exception as exc:
                print(f"[v5_sentinel.profit_alerts] ERROR: {exc}")
            time.sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        mt5_positions.shutdown()


if __name__ == "__main__":
    main()
