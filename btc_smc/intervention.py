"""Distinguishes manual (client/mobile/web) order cancellations and position
closes from bot-initiated or SL/TP/stop-out ones, using MT5's own reason
codes via history lookups rather than tracking "did the bot just do this"
flags -- straightforward since we're polling anyway.
"""
from __future__ import annotations

import MetaTrader5 as mt5

from btc_smc.candidates import parse_order_comment

MANUAL_ORDER_REASONS = (mt5.ORDER_REASON_CLIENT, mt5.ORDER_REASON_MOBILE, mt5.ORDER_REASON_WEB)
MANUAL_DEAL_REASONS = (mt5.DEAL_REASON_CLIENT, mt5.DEAL_REASON_MOBILE, mt5.DEAL_REASON_WEB)


def _source_tf(zone_key: str) -> str:
    return zone_key.split("|", 1)[0]


def check_manual_pending_cancellations(disappeared_tickets: set) -> list:
    """disappeared_tickets: pending-order tickets seen last poll but gone now.
    Returns [(source_tf, zone_key), ...] for the ones a manual cancel (not a
    fill, not the bot's own replace) actually removed."""
    results = []
    for ticket in disappeared_tickets:
        history = mt5.history_orders_get(ticket=ticket)
        if not history:
            continue
        order = history[0]

        if order.state == mt5.ORDER_STATE_FILLED:
            continue  # a fill, not a cancellation -- sync_filled_zones handles this

        if order.reason not in MANUAL_ORDER_REASONS:
            continue  # bot-initiated (EXPERT) or SL/TP/stop-out -- no block

        parsed = parse_order_comment(order.comment)
        if parsed is None:
            continue  # not one of ours

        zone_key, _event_time = parsed
        results.append((_source_tf(zone_key), zone_key))

    return results


def check_manual_position_closes(disappeared_tickets: set) -> list:
    """disappeared_tickets: position tickets seen last poll but gone now.
    Returns [(source_tf, zone_key), ...] for the ones a manual close (not
    SL/TP/stop-out, not the bot's own bias-flip close) actually closed.

    The exit deal's `reason` (set by MT5 itself) tells us whether the close
    was manual -- but MT5 does NOT preserve our comment on a manually
    triggered close, so the zone identity has to come from the position's
    original entry deal instead."""
    results = []
    for ticket in disappeared_tickets:
        deals = mt5.history_deals_get(position=ticket) or ()
        entry_deals = [d for d in deals if d.entry == mt5.DEAL_ENTRY_IN]
        exit_deals = [d for d in deals if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)]
        if not entry_deals or not exit_deals:
            continue

        if exit_deals[-1].reason not in MANUAL_DEAL_REASONS:
            continue  # bot-initiated (EXPERT), SL, TP, or stop-out -- no block

        parsed = parse_order_comment(entry_deals[0].comment)
        if parsed is None:
            continue  # not one of ours

        zone_key, _event_time = parsed
        results.append((_source_tf(zone_key), zone_key))

    return results
