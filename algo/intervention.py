"""Distinguishes manual (client/mobile/web) order cancellations and position
closes from bot-initiated or SL/TP/stop-out ones.

IMPORTANT: MT5's ORDER_REASON field reflects who/what CREATED an order, not
who cancelled it -- it never changes after the order is placed. Since every
order we ever see was created by this bot via the API, ORDER_REASON is
*always* EXPERT for our orders regardless of who cancels them later, making
it useless for detecting a manual cancellation (confirmed live: a user's own
manual cancellations showed up as reason=EXPERT). Pending-order cancellation
detection therefore tracks "did the bot itself just request this specific
cancellation" directly instead -- anything that disappears without the bot
having asked for it is treated as manual.

Position closes don't have this problem: DEAL_REASON is on the closing
DEAL itself (a fresh event created at that exact moment), not on a
pre-existing object, so it correctly reflects who triggered that specific
close.
"""
from __future__ import annotations

import MetaTrader5 as mt5

from algo.candidates import parse_order_comment

MANUAL_DEAL_REASONS = (mt5.DEAL_REASON_CLIENT, mt5.DEAL_REASON_MOBILE, mt5.DEAL_REASON_WEB)


def _source_tf(zone_key: str) -> str:
    return zone_key.split("|", 1)[0]


MANUAL_CANCEL_GRACE_POLLS = 3


def _order_direction(order) -> int:
    return 1 if order.type in (mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_BUY_STOP) else -1


def check_manual_pending_cancellations(disappeared_tickets: set, expected_cancellations: set,
                                       current_pending_setups: set, pending_confirm: dict) -> list:
    """disappeared_tickets: pending-order tickets seen last poll but gone now.
    expected_cancellations: tickets the bot itself just cancelled.
    current_pending_setups: {(direction, price_open, sl), ...} for every
    pending order live RIGHT NOW, regardless of comment.
    pending_confirm: caller's persistent dict (ticket -> [setup, zone_key,
    remaining_grace_polls]) for disappearances still awaiting confirmation.

    Returns [(source_tf, zone_key), ...] for disappearances CONFIRMED manual
    -- i.e. still genuinely gone after a grace window, not explained by any
    mechanism this bot understands.

    Not in expected_cancellations is NOT sufficient evidence of a manual
    cancel by itself. Confirmed live: MT5's IPC layer can silently swap a
    pending order's ticket ID -- cancel the old one, create a new one -- while
    resubmitting what is, to the terminal, the identical request. Neither
    side of that swap goes through our own cancel_pending_order() call, so
    expected_cancellations can't catch it, and the old ticket disappearing
    looks identical to a real manual cancel from here. A real manual cancel
    leaves nothing behind; this leaves a same-setup (direction, price, SL)
    order behind, either immediately or within the next poll or two. Only a
    disappearance with no matching live order even after
    MANUAL_CANCEL_GRACE_POLLS extra polls gets treated as confirmed manual --
    a false "manual" block defeats the entire point of this safeguard, so
    it must never fire on a disappearance this bot can otherwise explain."""
    results = []
    newly_tracked = set()

    for ticket in disappeared_tickets:
        if ticket in expected_cancellations:
            expected_cancellations.discard(ticket)
            continue  # the bot itself cancelled this -- not manual

        history = mt5.history_orders_get(ticket=ticket)
        if not history:
            continue
        order = history[0]

        if order.state == mt5.ORDER_STATE_FILLED:
            continue  # a fill, not a cancellation -- sync_filled_zones handles this

        setup = (_order_direction(order), order.price_open, order.sl)
        if setup in current_pending_setups:
            continue  # already resubmitted under a new ticket -- not manual

        parsed = parse_order_comment(order.comment)
        if parsed is None:
            continue  # not one of ours

        zone_key, _event_time = parsed
        pending_confirm[ticket] = [setup, zone_key, MANUAL_CANCEL_GRACE_POLLS]
        newly_tracked.add(ticket)

    resolved = []
    for ticket, (setup, zone_key, remaining) in pending_confirm.items():
        if ticket in newly_tracked:
            continue  # just started tracking this cycle -- give it its full grace window

        if setup in current_pending_setups:
            resolved.append(ticket)  # a delayed resubmission caught up -- not manual
            continue
        if remaining <= 1:
            results.append((_source_tf(zone_key), zone_key))  # confirmed manual
            resolved.append(ticket)
        else:
            pending_confirm[ticket][2] = remaining - 1

    for ticket in resolved:
        pending_confirm.pop(ticket, None)

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
