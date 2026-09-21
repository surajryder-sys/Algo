"""V6-Sentinel Exit Manager -- a standalone process that watches TM-STR, RM-STR and RM-ICT
and force-closes another component's open position the moment a component fires a REAL,
FILLED entry in the OPPOSITE direction. Built 2026-09-21 (user: "yes build exit manager")
after RM-STR and RM-ICT were seen holding a SELL and a BUY together with nothing linking
them; ported from v5_sentinel/exit_manager.py (same reasoning, see below) with two changes.

RULE: a watched component's fresh entry closes another watched component's open position
that sits in the OPPOSITE direction -- but ONLY IF the new trade's timeframe is the same as or
HIGHER than that position's (user, 2026-09-21: "smaller time reversal trade cannot close the
htf trade, but a htf trade can close a smaller time frame trade"). So an M5 zone or M3 line
trade can never close an M30/H1/H4 trade, while an H1 trade can close an M5 one. Equal
timeframes are allowed to close each other (the rule only forbids SMALLER closing BIGGER --
assumption, easy to flip). A trade's timeframe is: RM-STR the level's timeframe, RM-ICT the
zone's timeframe (both read from the trade's journal entry), TM-STR its M15 primary-structure
timeframe (a fixed value from trend_config.bias_timeframe -- assumption). A position whose
timeframe can't be determined is never closed. A component's own opposite positions stay that
component's own business (RM already squares off its own opposite trade internally).
Same-direction positions are never touched.

WHY THIS WATCHES THE TRADE JOURNALS: every bot already writes a REAL, CONFIRMED fill to its
own trade journal (trade_journal.py, "entry" event -- only genuine fills, never a
decision-only run) the instant its order goes through. Re-deriving each component's signal
here would mean copying four rule systems and their state, all racing the owning processes'
own files. So this process only tails the journals (one persisted watermark per source, so a
restart never replays history) and reads positions by magic number -- both read-only.

DIFFERENCES FROM V5S:
  - OLDER POSITIONS ONLY: an entry only closes opposite positions that were opened BEFORE it.
    Two opposite entries in the same poll cannot close each other -- the later fill wins
    (V5S would have closed both).
  - The close is recorded in the closed trade's OWN journal ("exit_requested", reason
    EXITMANAGER, caused_by which component/ticket) so its exit line explains why.

Never opens a position itself -- pure exit-only. Closes each target's full current volume via
broker.close_position(), comment "V6S-XM-SQ-{source}".

Run with: python -m v6_sentinel.exit_manager

Safety: V6S_EM_{SYMBOL}_ENABLE_TRADING must be explicitly true for any close to be sent.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import MetaTrader5 as mt5

from v6_sentinel import broker, config, decision_log, heartbeat, trade_journal
from v6_sentinel.exit_manager_config import ExitManagerSymbolConfig, WatchedSource, load_symbol_config

_DIR_LABEL = {1: "BUY", -1: "SELL"}


class WatermarkStore:
    """Per source: a coarse timestamp floor (a first-ever run bootstraps to "now", never 0, so
    history is never replayed) AND the exact tickets already reacted to. The ticket set is what
    guarantees correctness -- journal timestamps are 1-second resolution and several records can
    share one, so a pure ts comparison could skip a genuinely new entry."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._since_ts: dict[str, int] = {}
        self._seen: dict[str, list[int]] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
            self._since_ts = {k: int(v) for k, v in raw.get("since_ts", {}).items()}
            self._seen = {k: list(v) for k, v in raw.get("seen_tickets", {}).items()}
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self._since_ts, self._seen = {}, {}

    def _save(self) -> None:
        self._path.write_text(json.dumps({"since_ts": self._since_ts, "seen_tickets": self._seen}))

    def bootstrap_missing(self, names: list[str]) -> None:
        now = int(time.time())
        changed = False
        for name in names:
            if name not in self._since_ts:
                self._since_ts[name] = now
                changed = True
        if changed:
            self._save()

    def since_ts(self, name: str) -> int:
        return self._since_ts.get(name, int(time.time()))

    def is_processed(self, name: str, ticket: int) -> bool:
        return ticket in self._seen.get(name, [])

    def mark_processed(self, name: str, ticket: int, ts: int) -> None:
        tickets = self._seen.setdefault(name, [])
        if ticket not in tickets:
            tickets.append(ticket)
        if ts > self._since_ts.get(name, 0):
            self._since_ts[name] = ts
        self._save()


def _new_entries(source: WatchedSource, since: int, watermarks: WatermarkStore) -> list[dict]:
    """Unprocessed journal "entry" records for one source, oldest first."""
    path = Path(source.journal_file)
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("event") != "entry" or rec.get("ts", 0) < since:
            continue
        ticket = rec.get("ticket")
        if ticket is None or watermarks.is_processed(source.name, ticket):
            continue
        out.append({**rec, "_source": source})
    return out


def _timeframe_of(source: WatchedSource, logic: dict) -> Optional[int]:
    """Timeframe rank (in minutes) of one trade of `source`, from its journal entry's "logic"."""
    if source.fixed_timeframe_minutes is not None:
        return source.fixed_timeframe_minutes
    return trade_journal.timeframe_of_logic(logic)


def _position_timeframe(source: WatchedSource, ticket: int) -> Optional[int]:
    """Timeframe of an open position, read from its own journal entry (None if it has none)."""
    if source.fixed_timeframe_minutes is not None:
        return source.fixed_timeframe_minutes
    try:
        lines = Path(source.journal_file).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("event") == "entry" and rec.get("ticket") == ticket:
            return _timeframe_of(source, rec.get("logic") or {})
    return None


def _close_position(cfg: ExitManagerSymbolConfig, position, target: WatchedSource, cause: dict) -> bool:
    direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    caused_by = cause["_source"].name
    print(f"[V6S-XM] closing {target.name} #{position.ticket} ({_DIR_LABEL[direction]}) "
          f"-- {caused_by} #{cause['ticket']} just opened {cause['direction']}")
    if not cfg.enable_trading:
        print("[V6S-XM] enable_trading is false -- decision only, no order sent")
        decision_log.log(cfg.decision_log_file, "close_decision_only", ticket=position.ticket, target=target.name,
                         direction=_DIR_LABEL[direction], caused_by=caused_by, caused_by_ticket=cause["ticket"])
        return True
    result = broker.close_position(cfg.symbol, position, cfg.deviation_points, comment=f"V6S-XM-SQ-{caused_by}")
    if not result.ok:
        print(f"[V6S-XM] close failed: retcode={result.retcode} comment={result.comment}")
        decision_log.log(cfg.decision_log_file, "close_failed", ticket=position.ticket, target=target.name,
                         caused_by=caused_by, retcode=result.retcode)
        return False
    decision_log.log(cfg.decision_log_file, "close_filled", ticket=position.ticket, target=target.name,
                     direction=_DIR_LABEL[direction], caused_by=caused_by, caused_by_ticket=cause["ticket"])
    # Tell the closed trade's OWN journal why it ended (its owner reads this when it writes the exit line).
    trade_journal.TradeJournal(target.journal_file, target.name, cfg.symbol).exit_requested(
        position.ticket, "EXITMANAGER",
        {"rule": "another component opened the opposite direction", "caused_by": caused_by,
         "caused_by_ticket": cause["ticket"], "caused_by_direction": cause["direction"]})
    return True


def _handle_entry(cfg: ExitManagerSymbolConfig, entry: dict) -> None:
    """Close every OTHER source's position that is opposite to this entry AND was opened before it."""
    source: WatchedSource = entry["_source"]
    entry_tf = _timeframe_of(source, entry.get("logic") or {})
    opposite_type = mt5.POSITION_TYPE_SELL if entry["direction"] == "BUY" else mt5.POSITION_TYPE_BUY
    for other in cfg.sources:
        if other.name == source.name:
            continue
        for position in broker.get_positions(cfg.symbol, other.magic_number):
            if position.type != opposite_type or position.time >= entry["ts"]:
                continue
            target_tf = _position_timeframe(other, position.ticket)
            if entry_tf is None or target_tf is None:
                print(f"[V6S-XM] not closing {other.name} #{position.ticket}: timeframe unknown "
                      f"(entry M{entry_tf}, position M{target_tf})")
                continue
            if entry_tf < target_tf:
                print(f"[V6S-XM] not closing {other.name} #{position.ticket} (M{target_tf}): the new "
                      f"{source.name} trade is on a SMALLER timeframe (M{entry_tf}) -- an HTF trade is never "
                      f"closed by a smaller one")
                decision_log.log(cfg.decision_log_file, "close_blocked_by_timeframe", ticket=position.ticket,
                                 target=other.name, target_tf=target_tf, caused_by=source.name,
                                 caused_by_ticket=entry["ticket"], entry_tf=entry_tf)
                continue
            _close_position(cfg, position, other, entry)


def run_once(cfg: ExitManagerSymbolConfig, watermarks: WatermarkStore) -> None:
    watermarks.bootstrap_missing([s.name for s in cfg.sources])
    fresh: list[dict] = []
    for source in cfg.sources:
        fresh.extend(_new_entries(source, watermarks.since_ts(source.name), watermarks))
    for entry in sorted(fresh, key=lambda r: (r["ts"], r["ticket"])):      # oldest first, across components
        if entry.get("direction") in ("BUY", "SELL"):
            print(f"[V6S-XM] {entry['_source'].name} entered {entry['direction']} #{entry['ticket']} "
                  f"-- checking opposite positions")
            _handle_entry(cfg, entry)
        watermarks.mark_processed(entry["_source"].name, entry["ticket"], entry["ts"])


def main() -> None:
    cfgs = [load_symbol_config(symbol) for symbol in config.ACTIVE_SYMBOLS]
    stores = {c.symbol: WatermarkStore(c.state_file) for c in cfgs}
    for c in cfgs:
        print(f"[V6S-XM] {c.symbol} starting -- enable_trading={c.enable_trading} poll={c.poll_seconds}s "
              f"watching={[s.name for s in c.sources]}")
        broker.connect(c.symbol, c.mt5_terminal_path, c.mt5_login, c.mt5_password, c.mt5_server)
    try:
        while True:
            for c in cfgs:
                try:
                    run_once(c, stores[c.symbol])
                except Exception as exc:  # noqa: BLE001 -- keep the loop alive, log and continue
                    print(f"[V6S-XM] {c.symbol} cycle error: {exc!r}")
                heartbeat.write(c.heartbeat_file)
            time.sleep(min(c.poll_seconds for c in cfgs))
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
