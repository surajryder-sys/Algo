"""Exit Manager -- a standalone process that watches ALL FOUR
V5-Sentinel components (TM-STR, RM-STR, RM-ICT, TM-ICT) and force-closes
one component's own open position the moment ANY OTHER component fires
a real, filled entry in the opposite direction. Confirmed with the user
2026-09-15, prompted by a real live gap: TM-STR was long while RM-ICT
and TM-ICT both opened short, and nothing closed TM-STR's own position
-- "ICT should square off opposite side STR or ICT trades, when it
qualifies" -- then, once scoped, "lets build exit manager seperately...
that can watch everythting all components and take decisions on making
an exit."

SCOPE (fully symmetric, confirmed): ANY of the four components' own
fresh entries can force-close ANY of the OTHER three's own opposite
position -- not just ICT-sourced entries, and not paired
(TM-ICT<->TM-STR only) the way each component's OWN internal same-
component square-off already is. TM-STR opening long can close RM-ICT's
own short just as readily as the reverse.

WHY THIS WATCHES DECISION LOGS, NOT RAW SIGNALS: each component already
computes its own entry signals (RM's own find_signals()/
find_ict_signals(), TM-ICT's own find_signals(), TM-STR's own inline M3
logic) using triggers with wildly different shapes -- some privileged/
momentary (a fresh bar-close flip, valid for exactly one bar), some
continuously-live (TM-ICT's MO/PB, TM-STR's own 3Q/3P qualifying-price
race, valid for as long as price sits in range). Re-deriving all four
of those independently here would mean duplicating four different rule
systems and keeping them in permanent lockstep with whatever each
component's own file does next -- a maintenance trap, and a real risk
of silently drifting out of sync. Worse, actually recomputing those
signals would need this process to own ANOTHER copy of every
component's own trackers/HTF-state/OB-block reads, all racing against
the REAL owning process's own state files (BridgeBarFlipTracker writes
its whole file on every call -- two processes doing that to the SAME
path is exactly the kind of corruption a stray test copy in this
project's own history already demonstrated).

So instead: every component already logs "entry_filled" (a REAL,
CONFIRMED fill, decision_log.py, one line per component) the instant
its own order actually goes through. Exit Manager just tails those four
log files (decision_log.read_since(), one watermark per source,
persisted so a restart never replays old history) and reacts to a
CONFIRMED fill, not a re-derived guess at "does a signal currently
qualify." This also means Exit Manager never touches another
component's own eligibility bookkeeping (LevelEligibilityStore,
ICTEligibilityStore, M5FlipEligibility, ict_ob_block's own zone store)
at all -- it only ever reads positions (broker.get_positions, scoped by
magic number) and each component's own decision log, both read-only.

Deliberately does NOT react to "entry_decision_only" records (a
component whose OWN enable_trading is false, printing what it WOULD
have done) -- only a genuinely CONFIRMED "entry_filled" should ever
close a real position elsewhere. A hypothetical decision from a
dry-run component must never cause a real account action.

Deliberately never opens a position itself -- pure exit-only, matching
the name and this project's own original v3 architecture split
(Trend/Reversal Managers decide entries; Stoploss/Exit is a separate
execution-side concern). Closing uses each target position's OWN full
current volume (no partial-close semantics here) via
broker.close_position(), comment "V5S-XM-SQ-{source}" (V5S- prefix
added 2026-09-15, "all comments to follow V5S prefix") so it's traceable
back to which component's own entry caused the close.

Run with: python -m v5_sentinel.exit_manager

Safety: V5S_EXIT_MANAGER_ENABLE_TRADING must be explicitly true in .env
for any close to actually be sent -- independent of every other
component's own enable_trading flag.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import MetaTrader5 as mt5

from v5_sentinel import broker, decision_log, heartbeat
from v5_sentinel.exit_manager_config import Config, WatchedSource, load_config

_DIR_LABEL = {1: "BUY", -1: "SELL"}


class WatermarkStore:
    """Persists, PER SOURCE: a coarse timestamp floor (so a restart never
    re-scans the full historical log -- a first-ever run, or a brand-new
    source, bootstraps to "now" rather than 0, see bootstrap_missing())
    AND the exact set of tickets already reacted to. The ticket set is
    what actually guarantees correctness -- decision-log timestamps are
    only 1-second resolution and this project's own logs routinely carry
    several records in the same second, so a pure ts>=watermark
    comparison could silently skip a genuinely NEW entry_filled landing
    in the same second as one already handled. Tickets are always unique
    (MT5 never reuses one), so "have I handled this exact ticket" is
    exact regardless of timestamp collisions. Not pruned -- at this
    project's actual volume (tens of fills a day) the persisted set
    stays trivially small; revisit only if that changes."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._since_ts: dict[str, int] = {}
        self._seen_tickets: dict[str, list[int]] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
            self._since_ts = {k: int(v) for k, v in raw.get("since_ts", {}).items()}
            self._seen_tickets = {k: list(v) for k, v in raw.get("seen_tickets", {}).items()}
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self._since_ts = {}
            self._seen_tickets = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps({"since_ts": self._since_ts, "seen_tickets": self._seen_tickets}))

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
        return ticket in self._seen_tickets.get(name, [])

    def mark_processed(self, name: str, ticket: int, ts: int) -> None:
        tickets = self._seen_tickets.setdefault(name, [])
        if ticket not in tickets:
            tickets.append(ticket)
        if ts > self._since_ts.get(name, 0):
            self._since_ts[name] = ts
        self._save()


def _close_position(cfg: Config, position, source_name: str) -> bool:
    direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    print(f"[V5S-XM] closing #{position.ticket} ({_DIR_LABEL[direction]}, magic={position.magic}) "
          f"-- {source_name} just fired opposite")
    if not cfg.enable_trading:
        print("[V5S-XM] enable_trading is false -- decision only, no order sent")
        decision_log.log(cfg.decision_log_file, "close_decision_only", ticket=position.ticket,
                         direction=_DIR_LABEL[direction], magic=position.magic, caused_by=source_name)
        return True
    result = broker.close_position(cfg.symbol, position, cfg.deviation_points, comment=f"V5S-XM-SQ-{source_name}")
    if not result.ok:
        print(f"[V5S-XM] close failed: retcode={result.retcode} comment={result.comment}")
        decision_log.log(cfg.decision_log_file, "close_failed", ticket=position.ticket,
                         direction=_DIR_LABEL[direction], magic=position.magic, caused_by=source_name,
                         retcode=result.retcode)
        return False
    print(f"[V5S-XM] closed #{position.ticket}")
    decision_log.log(cfg.decision_log_file, "close_filled", ticket=position.ticket,
                     direction=_DIR_LABEL[direction], magic=position.magic, caused_by=source_name)
    return True


def _handle_fresh_entry(cfg: Config, source: WatchedSource, entry_direction: str) -> None:
    """entry_direction is the STRING "BUY"/"SELL" as decision_log itself
    records it. Closes every OTHER source's own currently-open position
    that sits in the OPPOSITE direction -- every match, not just the
    first, since more than one other component could plausibly be
    holding the opposite side at once."""
    opposite_type = mt5.POSITION_TYPE_SELL if entry_direction == "BUY" else mt5.POSITION_TYPE_BUY
    for other in cfg.sources:
        if other.name == source.name:
            continue
        for position in broker.get_positions(cfg.symbol, other.magic_number):
            if position.type == opposite_type:
                _close_position(cfg, position, source.name)


def run_once(cfg: Config, watermarks: WatermarkStore) -> None:
    watermarks.bootstrap_missing([s.name for s in cfg.sources])

    for source in cfg.sources:
        since = watermarks.since_ts(source.name)
        records = decision_log.read_since(source.decision_log_file, since)
        for rec in records:
            if rec.get("kind") != "entry_filled":
                continue
            if source.component_filter is not None and rec.get("component") != source.component_filter:
                continue
            ticket = rec.get("ticket")
            if ticket is None or watermarks.is_processed(source.name, ticket):
                continue
            direction = rec.get("direction")
            if direction not in ("BUY", "SELL"):
                watermarks.mark_processed(source.name, ticket, rec.get("ts", since))
                continue
            print(f"[V5S-XM] {source.name} entry_filled {direction} @ {rec.get('ts')} (#{ticket}) "
                  f"-- checking opposite positions")
            _handle_fresh_entry(cfg, source, direction)
            watermarks.mark_processed(source.name, ticket, rec.get("ts", since))


def main() -> None:
    cfg = load_config()
    print(f"[V5S-XM] starting -- symbol={cfg.symbol} enable_trading={cfg.enable_trading} "
          f"poll={cfg.poll_seconds}s watching={[s.name for s in cfg.sources]}")

    broker.connect(cfg)
    watermarks = WatermarkStore(cfg.state_file)

    try:
        while True:
            try:
                run_once(cfg, watermarks)
            except Exception as exc:  # noqa: BLE001 -- keep the loop alive, log and continue
                print(f"[V5S-XM] cycle error: {exc!r}")
            heartbeat.write(cfg.heartbeat_file)
            time.sleep(cfg.poll_seconds)
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
