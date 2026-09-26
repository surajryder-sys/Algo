"""Major/Minor Levels Watcher -- Data Manager's 4th thread (2026-09-26).

DATA IMPORT ONLY (user's own words: "lets implement the data import from
major minor, logics lets define later") -- this module computes and
persists Major/Minor Support/Resistance per timeframe so the bot "keeps
seeing" it; no entry/exit decision is made anywhere in this file. TM-STR/
RM-ICT consuming this to gate their own entries is a separate, later
change -- read_major_minor_state() below is the ready-made reader for
that, not called from anywhere yet.

WHAT "MAJOR/MINOR" IS (confirmed with the user 2026-09-26 after an
initial wrong guess -- see this session's own back-and-forth): NOT the
ATR dual-trail lines RM-STR used to trade off (that pair is just called
line1/line2 in mql5/ATR_Trial_Dual_SuperTrend_Major_Minor_HammerStar.mq5).
"Major/Minor" is that same indicator's SEPARATE ZigZag-based swing-
structure Support/Resistance block -- which only ever draws chart rays,
with NO bridge-JSON publish path at all, so it can't be read live from
MT5 no matter what. rates.read_major_minor()/read_all_major_minor() is
the only place this concept already existed in this codebase: a direct,
indicator-free port of mql5/MajorMinor_Secret_ShortTerm.mq5's ZigZag +
Major/Minor promotion logic, computed straight from
mt5.copy_rates_from_pos(). This module is just the "keep it fresh and
persisted" wrapper around that already-built, previously-unused function.

WHY ITS OWN SLOW THREAD, NOT INLINE ANYWHERE ELSE: measured live against
XAUUSD, rates.read_all_major_minor() -- all 8 of its own target
timeframes (H4, H2, H1, M30, M15, M5, M3, M1), bar_count=3000 each --
takes ~9 SECONDS end to end (the ZigZag/Major-Minor-promotion
classification is a genuine per-bar Python loop, not vectorized).
config.POLL_SECONDS (the main Trade Manager/Data Manager cadence) is 1
second -- computing this inline anywhere in that loop would blow out
every other sub-component's own cycle badly. Same problem
ict_ob_watcher.py/nlb_nsb_watcher.py/tv_scraper already solve for other
expensive, not-every-second derived state: its own thread, its own much
slower cadence (V7S_MAJOR_MINOR_POLL_SECONDS, default 60s), a state file
+ heartbeat, read cheaply (no recompute) by whatever consumes it later.

Purely data-tracking, same category as tv_scraper/nlb_nsb_watcher/
ict_ob_watcher -- no MT5 orders are ever placed here. Read-only against
the broker (copy_rates only); only ever writes its own state/heartbeat
files.

ALIGNING SUPPORT / ALIGNING RESISTANCE (added 2026-09-26, user's own
terms): when a Major or Minor level from one timeframe sits at the EXACT
SAME price as a Major or Minor level (same support/resistance side) from
a DIFFERENT timeframe. Confirmed with the user: "only exact major minor
values can align" -- no tolerance/zone, exact float value match only.
Major and Minor can align together too (e.g. H4's Major Support and
M15's Minor Support at the identical price still counts as one Aligning
Support cluster) -- only the support/resistance SIDE has to match, not
major-vs-minor. Computed fresh every cycle alongside the raw snapshots
and persisted in the same state file; see compute_alignments() below.

Run with: python -m v7_sentinel.major_minor_watcher
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import MetaTrader5 as mt5
from dotenv import load_dotenv

from v7_sentinel import config, heartbeat, rates

load_dotenv()

_POLL_SECONDS = float(os.getenv("V7S_MAJOR_MINOR_POLL_SECONDS", "60"))


@dataclass(frozen=True)
class WatcherSymbolConfig:
    symbol: str
    state_file: str
    heartbeat_file: str
    poll_seconds: float


def load_symbol_configs() -> list[WatcherSymbolConfig]:
    return [
        WatcherSymbolConfig(
            symbol=symbol,
            state_file=config.state_file_for("major_minor", symbol),
            heartbeat_file=config.state_file_for("major_minor_watcher_heartbeat", symbol),
            poll_seconds=_POLL_SECONDS,
        )
        for symbol in config.ACTIVE_SYMBOLS
    ]


def _snapshot_to_dict(snapshot: Optional[rates.MajorMinorSnapshot]) -> Optional[dict]:
    return None if snapshot is None else asdict(snapshot)


def _dict_to_level(raw: Optional[dict]) -> Optional[rates.MajorMinorLevel]:
    return None if raw is None else rates.MajorMinorLevel(value=raw["value"], time=raw["time"])


def _dict_to_snapshot(raw: Optional[dict]) -> Optional[rates.MajorMinorSnapshot]:
    if raw is None:
        return None
    return rates.MajorMinorSnapshot(
        symbol=raw["symbol"],
        timeframe_minutes=raw["timeframe_minutes"],
        updated=raw["updated"],
        major_support=_dict_to_level(raw["major_support"]),
        major_resistance=_dict_to_level(raw["major_resistance"]),
        minor_support=_dict_to_level(raw["minor_support"]),
        minor_resistance=_dict_to_level(raw["minor_resistance"]),
    )


@dataclass(frozen=True)
class AlignmentMember:
    timeframe_minutes: int
    kind: str   # "major" or "minor"


@dataclass(frozen=True)
class AlignmentCluster:
    value: float
    members: tuple[AlignmentMember, ...]


def _gather_side(snapshots: dict[int, Optional[rates.MajorMinorSnapshot]], side: str) -> dict[float, list[AlignmentMember]]:
    by_value: dict[float, list[AlignmentMember]] = {}
    for tf, snap in snapshots.items():
        if snap is None:
            continue
        major = snap.major_support if side == "support" else snap.major_resistance
        minor = snap.minor_support if side == "support" else snap.minor_resistance
        if major is not None:
            by_value.setdefault(major.value, []).append(AlignmentMember(tf, "major"))
        if minor is not None:
            by_value.setdefault(minor.value, []).append(AlignmentMember(tf, "minor"))
    return by_value


def compute_alignments(
    snapshots: dict[int, Optional[rates.MajorMinorSnapshot]],
) -> tuple[list[AlignmentCluster], list[AlignmentCluster]]:
    """(aligning_support, aligning_resistance) -- see module docstring's
    own ALIGNING SUPPORT / ALIGNING RESISTANCE section for the exact
    rule. Only values shared by 2+ (timeframe, major/minor) members are
    a cluster -- a level nobody else shares isn't an alignment."""
    clusters = {}
    for side in ("support", "resistance"):
        by_value = _gather_side(snapshots, side)
        clusters[side] = [
            AlignmentCluster(value=value, members=tuple(members))
            for value, members in sorted(by_value.items())
            if len(members) >= 2
        ]
    return clusters["support"], clusters["resistance"]


def _cluster_to_dict(cluster: AlignmentCluster) -> dict:
    return {"value": cluster.value, "members": [asdict(m) for m in cluster.members]}


def _dict_to_cluster(raw: dict) -> AlignmentCluster:
    return AlignmentCluster(
        value=raw["value"],
        members=tuple(AlignmentMember(m["timeframe_minutes"], m["kind"]) for m in raw["members"]),
    )


def run_once(cfg: WatcherSymbolConfig) -> None:
    snapshots = rates.read_all_major_minor(cfg.symbol)
    ok = sum(1 for s in snapshots.values() if s is not None)
    aligning_support, aligning_resistance = compute_alignments(snapshots)
    print(f"[V7S-MAJORMINOR] {cfg.symbol} recomputed -- {ok}/{len(snapshots)} timeframes have data, "
          f"{len(aligning_support)} aligning support level(s), {len(aligning_resistance)} aligning resistance level(s)")
    payload = {
        "levels": {str(tf): _snapshot_to_dict(snap) for tf, snap in snapshots.items()},
        "aligning_support": [_cluster_to_dict(c) for c in aligning_support],
        "aligning_resistance": [_cluster_to_dict(c) for c in aligning_resistance],
    }
    Path(cfg.state_file).write_text(json.dumps(payload))


def _read_state_file(symbol: str) -> Optional[dict]:
    path = Path(config.state_file_for("major_minor", symbol))
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def read_major_minor_state(symbol: str) -> dict[int, Optional[rates.MajorMinorSnapshot]]:
    """Cheap reader side -- no recompute, just deserializes whatever this
    watcher last persisted. Empty dict if the state file doesn't exist
    yet (watcher never completed a cycle for this symbol). Not called
    from anywhere yet -- ready for TM-STR/RM-ICT once the "logics" phase
    defines how this data actually gates an entry."""
    raw = _read_state_file(symbol)
    if raw is None:
        return {}
    return {int(tf): _dict_to_snapshot(snap) for tf, snap in raw["levels"].items()}


def read_alignments(symbol: str) -> tuple[list[AlignmentCluster], list[AlignmentCluster]]:
    """Cheap reader side for Aligning Support/Resistance -- no recompute,
    same "empty if never persisted yet" contract as read_major_minor_state().
    Not called from anywhere yet -- data import only, see module docstring."""
    raw = _read_state_file(symbol)
    if raw is None:
        return [], []
    return (
        [_dict_to_cluster(c) for c in raw["aligning_support"]],
        [_dict_to_cluster(c) for c in raw["aligning_resistance"]],
    )


def main() -> None:
    symbol_configs = load_symbol_configs()
    print(f"[V7S-MAJORMINOR] starting -- symbols={[c.symbol for c in symbol_configs]} "
          f"poll={_POLL_SECONDS}s timeframes={rates.TARGET_TIMEFRAMES_MINUTES}")

    if not mt5.initialize(path=config.MT5_TERMINAL_PATH):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    try:
        while True:
            for cfg in symbol_configs:
                try:
                    run_once(cfg)
                except Exception as exc:  # noqa: BLE001 -- keep the loop alive, log and continue
                    print(f"[V7S-MAJORMINOR] {cfg.symbol} cycle error: {exc!r}")
                heartbeat.write(cfg.heartbeat_file)
            time.sleep(min(c.poll_seconds for c in symbol_configs))
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
