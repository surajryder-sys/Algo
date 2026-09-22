"""Per-trade journal: WHY every real trade was entered and WHY/HOW it ended,
kept in one place per component so any trade can be reviewed later.

The decision log (decision_log.py) already records what each bot decided,
but it is a flat stream: entries and exits aren't joined per trade, partial
closes and SL moves aren't in it at all, and a trade the BROKER closed (SL
hit, manual close) leaves no exit record anywhere. This journal fixes that.

One JSON-Lines file per component per symbol (config.state_file_for(...,
ext="jsonl"), gitignored). Every line carries "event", "ticket", "ts" (unix
wall clock) and "time_ist" (that moment in IST). Events, in order:

  entry           the trade opened -- "logic" holds everything that made the
                  signal qualify (bias source/time, CISD time, zone/level,
                  trigger, SL and where the SL came from, ...)
  partial         a Trade Manager partial close (label, volume, price)
  sl_move         the SL manager moved the SL (old -> new, price)
  exit_requested  THIS BOT is about to close the whole position and why
                  (M15 bias flip, M5 flip, square-off, opposite signal, ...)
  exit            the position is fully closed. Written by reconcile() once
                  the broker's deal history shows it: "exit_reason" (the bot's
                  own reason if it closed it, else the broker's -- SL_HIT,
                  TP_HIT, STOP_OUT, MANUAL), entry/exit price, net profit,
                  duration, plus a copy of the entry "logic" and every
                  partial/SL move, so the exit line alone tells the whole story.

Only real fills are journaled (a decision-only run has no ticket). Raw times
inside "logic" (bar/CISD event times) are the broker's server-time epochs;
"time_ist" on each line is real wall-clock IST.

State is rebuilt from the file at start-up (entries with no exit yet are the
open trades), so a restart loses nothing. reconcile() only finalizes a trade
when the deal history really shows it fully closed -- a momentary empty
positions list can never produce a false exit.

Never raises: a journal failure must not take the trading loop down.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path
from typing import Any, Iterable, Optional

import MetaTrader5 as mt5

_IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_RETRY_SECONDS = 30.0     # how often to re-check history for a ticket that is gone but not yet resolvable

_DEAL_REASON_LABEL = {
    getattr(mt5, "DEAL_REASON_SL", 4): "SL_HIT",
    getattr(mt5, "DEAL_REASON_TP", 5): "TP_HIT",
    getattr(mt5, "DEAL_REASON_SO", 6): "STOP_OUT",
    getattr(mt5, "DEAL_REASON_CLIENT", 0): "MANUAL",
    getattr(mt5, "DEAL_REASON_MOBILE", 1): "MANUAL",
    getattr(mt5, "DEAL_REASON_WEB", 2): "MANUAL",
    getattr(mt5, "DEAL_REASON_EXPERT", 3): "BOT_CLOSE_REASON_NOT_RECORDED",
}


_TF_NAME_MINUTES = {"D1": 1440, "H4": 240, "H2": 120, "H1": 60, "M30": 30, "M15": 15, "M10": 10,
                    "M5": 5, "M3": 3, "M1": 1}


def timeframe_of_logic(logic: dict) -> Optional[int]:
    """Timeframe rank in minutes of one trade, from its entry "logic": RM-STR records the level's
    timeframe_minutes, RM-ICT the zone id ("XAUUSD|240|bull|<start>") and a timeframe name. Used
    by the RM's own opposite-signal rule and by the Exit Manager, so both rank trades identically.
    None if the logic carries no timeframe."""
    tf = logic.get("timeframe_minutes")
    if tf is not None:
        return int(tf)
    zone_id = logic.get("zone_id")
    if zone_id:
        try:
            return int(str(zone_id).split("|")[1])
        except (IndexError, ValueError):
            pass
    return _TF_NAME_MINUTES.get(str(logic.get("timeframe_name")))


def _ist_now() -> str:
    return dt.datetime.now(_IST).strftime("%Y-%m-%d %H:%M:%S IST")


class TradeJournal:
    def __init__(self, path: str, component: str, symbol: str):
        self._path = Path(path)
        self._component = component
        self._symbol = symbol
        self._open: dict[int, dict] = {}      # ticket -> {"entry", "partials", "sl_moves", "pending_exit"}
        self._last_try: dict[int, float] = {}
        self._load()

    # ---- persistence ----
    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            try:
                rec = json.loads(line)
                ticket, event = int(rec["ticket"]), rec["event"]
            except (ValueError, KeyError, TypeError):
                continue
            if event == "entry":
                self._open[ticket] = {"entry": rec, "partials": [], "sl_moves": [], "pending_exit": None}
            elif ticket in self._open:
                if event == "partial":
                    self._open[ticket]["partials"].append(rec)
                elif event == "sl_move":
                    self._open[ticket]["sl_moves"].append(rec)
                elif event == "exit_requested":
                    self._open[ticket]["pending_exit"] = rec
                elif event == "exit":
                    del self._open[ticket]

    def _write(self, event: str, ticket: int, **fields: Any) -> dict:
        rec = {"event": event, "ticket": ticket, "component": self._component, "symbol": self._symbol,
               "ts": int(time.time()), "time_ist": _ist_now(), **fields}
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except OSError as exc:
            print(f"[V6S-JOURNAL] write failed: {exc!r} -- record was: {rec}")
        return rec

    # ---- what the bots call ----
    def entry(self, ticket: int, direction: str, lots: float, sl: float, comment: str, logic: dict) -> None:
        rec = self._write("entry", ticket, direction=direction, lots=lots, sl=sl, comment=comment, logic=logic)
        self._open[ticket] = {"entry": rec, "partials": [], "sl_moves": [], "pending_exit": None}

    def partial(self, ticket: int, label: str, volume: float, price: float) -> None:
        rec = self._write("partial", ticket, label=label, volume=volume, price=price)
        if ticket in self._open:
            self._open[ticket]["partials"].append(rec)

    def sl_move(self, ticket: int, old_sl: Optional[float], new_sl: float, price: float) -> None:
        rec = self._write("sl_move", ticket, old_sl=old_sl, new_sl=new_sl, price=price)
        if ticket in self._open:
            self._open[ticket]["sl_moves"].append(rec)

    def exit_requested(self, ticket: int, reason: str, detail: Optional[dict] = None) -> None:
        """The bot is closing the WHOLE position and this is why."""
        rec = self._write("exit_requested", ticket, reason=reason, detail=detail or {})
        if ticket in self._open:
            self._open[ticket]["pending_exit"] = rec

    def timeframe_minutes(self, ticket: int) -> Optional[int]:
        """Timeframe rank of an OPEN journaled trade (None if unknown / not journaled)."""
        info = self._open.get(ticket)
        return timeframe_of_logic(info["entry"].get("logic") or {}) if info else None

    def _pending_from_file(self, ticket: int) -> Optional[dict]:
        """An exit_requested line for this ticket written by ANOTHER process (the Exit Manager
        closes trades it does not own and records why here), newest first."""
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        for line in reversed(lines):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("event") == "exit_requested" and rec.get("ticket") == ticket:
                return rec
        return None

    def reconcile(self, open_tickets: Iterable[int]) -> list[dict]:
        """Call every cycle with the tickets currently open for this
        component. Writes the "exit" line for any journaled trade that is
        gone and whose closing shows in the broker's deal history. Returns
        the exit records written THIS call (empty list if none) -- e.g.
        reversal_main.py's SL-distance guard reads these for SL_HIT."""
        written: list[dict] = []
        open_set = set(open_tickets)
        now = time.time()
        for ticket in list(self._open):
            if ticket in open_set:
                continue
            if now - self._last_try.get(ticket, 0.0) < _RETRY_SECONDS:
                continue
            self._last_try[ticket] = now
            summary = _closing_summary(ticket)
            if summary is None:
                continue          # not (yet) provably closed -- try again later
            info = self._open[ticket]
            pending = info["pending_exit"] or self._pending_from_file(ticket)
            if pending is not None:
                reason, detail = pending.get("reason", "?"), pending.get("detail", {})
            else:
                reason, detail = _DEAL_REASON_LABEL.get(summary["deal_reason"], f"DEAL_REASON_{summary['deal_reason']}"), {}
            if reason == "SL_HIT":
                detail = {**detail, "sl_stage": "moved" if info["sl_moves"] else "initial",
                          "sl_at_close": info["sl_moves"][-1]["new_sl"] if info["sl_moves"] else info["entry"].get("sl")}
            rec = self._write("exit", ticket, exit_reason=reason, exit_detail=detail,
                        entry_price=summary["entry_price"], exit_price=summary["exit_price"],
                        volume=summary["volume"], profit_net=summary["profit_net"],
                        duration_s=summary["duration_s"], deal_reason=summary["deal_reason"],
                        partials=[{k: p[k] for k in ("time_ist", "label", "volume", "price")} for p in info["partials"]],
                        sl_moves=len(info["sl_moves"]),
                        direction=info["entry"].get("direction"), entry_time_ist=info["entry"].get("time_ist"),
                        entered_by=info["entry"].get("logic"))
            written.append(rec)
            del self._open[ticket]
            self._last_try.pop(ticket, None)
        return written


def _closing_summary(ticket: int) -> Optional[dict]:
    """The broker's own account of a position, or None unless it is provably
    FULLY closed (closing volume has reached the opening volume)."""
    try:
        deals = mt5.history_deals_get(position=ticket)
    except Exception:  # noqa: BLE001 -- history lookup must never break the loop
        return None
    if not deals:
        return None
    ins = [d for d in deals if d.entry == mt5.DEAL_ENTRY_IN]
    outs = [d for d in deals if d.entry in (mt5.DEAL_ENTRY_OUT, getattr(mt5, "DEAL_ENTRY_OUT_BY", 3))]
    if not ins or not outs:
        return None
    in_volume, out_volume = sum(d.volume for d in ins), sum(d.volume for d in outs)
    if out_volume < in_volume - 1e-9:
        return None
    final = max(outs, key=lambda d: (d.time, getattr(d, "time_msc", 0)))
    return {
        "entry_price": ins[0].price,
        "exit_price": final.price,
        "volume": in_volume,
        "profit_net": round(sum(d.profit + d.commission + d.swap + getattr(d, "fee", 0.0) for d in deals), 2),
        "duration_s": int(final.time - ins[0].time),
        "deal_reason": int(final.reason),
    }
