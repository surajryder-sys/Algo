"""Distinguishes manual (client/mobile/web) cancellations and closes
from Execution Bridge's own (cancel-and-replace, flip-close). v3's own
copy of algo_v2/intervention.py's approach, not an import (see
CLAUDE.md) -- same underlying MT5 quirk applies: ORDER_REASON reflects
who CREATED an order, not who cancelled it, so it's useless for this;
"did Execution Bridge itself just request this cancellation" is tracked
directly instead (order_tracker.py's expected_cancellations).

Position closes don't have that problem -- DEAL_REASON is on the
closing DEAL itself, a fresh event, so it correctly reflects who
triggered that specific close.
"""
from __future__ import annotations

from typing import Optional

import MetaTrader5 as mt5

from v3.execution_bridge.order_tracker import OrderTracker, parse_comment

MANUAL_DEAL_REASONS = (mt5.DEAL_REASON_CLIENT, mt5.DEAL_REASON_MOBILE, mt5.DEAL_REASON_WEB)


def check_pending_disappeared(ticket: int, tracker: OrderTracker) -> Optional[str]:
    """A tracked pending order's ticket is no longer in MT5's open
    orders. Returns "filled", "manual", or "unknown" -- never raises,
    since this always runs against a ticket we already know is gone."""
    if tracker.was_expected_cancellation(ticket):
        return "expected"  # Execution Bridge itself cancelled this -- not manual

    history = mt5.history_orders_get(ticket=ticket)
    if not history:
        return "unknown"
    order = history[0]

    if order.state == mt5.ORDER_STATE_FILLED:
        return "filled"
    return "manual"  # cancelled, and not by us -- the user pulled it


def check_position_disappeared(ticket: int) -> Optional[str]:
    """A tracked position's ticket is no longer open. Returns "manual",
    "sl", "tp", "bot" (Execution Bridge's own flip-close), or "unknown"."""
    deals = mt5.history_deals_get(position=ticket) or ()
    exit_deals = [d for d in deals if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)]
    if not exit_deals:
        return "unknown"

    reason = exit_deals[-1].reason
    if reason in MANUAL_DEAL_REASONS:
        return "manual"
    if reason == mt5.DEAL_REASON_SL:
        return "sl"
    if reason == mt5.DEAL_REASON_TP:
        return "tp"
    return "bot"  # DEAL_REASON_EXPERT -- Execution Bridge's own close
