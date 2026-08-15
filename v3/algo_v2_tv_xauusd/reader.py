"""Adapts tradingview_bot's ZoneStore/AtrStore -- fed by TWO independent,
uncoordinated sources (tv_bridge's alert webhooks, and tv_scraper's Data
Window polling, each writing its own separate files) -- into a single
merged OBSnapshot/ATRSnapshot-shaped view, so algo_v2's exact zone/
candidate/management logic (copied into this package basically unchanged
in zone.py/candidates.py/management.py) can run against it the same way it
runs against the MT5 indicator bridge (ob_bridge/atr_bridge) in algo_v2
itself.

Why two sources need merging here at all: alerts only ever report zones
formed/mitigated AFTER the alert was created (Pine's alert() doesn't fire
retroactively for bars that already closed) -- they can never report the
zones already sitting on the chart. tv_scraper has no such gap (it reads
whatever's currently on screen) but needs a permanently-open, logged-in
browser window to do it. Running both means neither gap is uncovered.

Both sources are kept in SEPARATE files (see tv_scraper/config.py's own
comment) because ZoneStore/AtrStore each load once into memory and
unconditionally overwrite the whole file on every save -- two processes
sharing one file would clobber each other's zones rather than combine them.
The merge happens here, at read time, instead.

Only exposes what the copied logic actually reads, confirmed by checking
every call site: OBSnapshot.bull/.bear (lists of Zone-shaped high/low/
virgin/start_time/detected_time/detected_price), and ATRSnapshot.
event_time/.trend. The MT5 indicator's real OBSnapshot/ATRSnapshot carry
several more aggregate fields (bias, latest_high, visit_time, trail_stop,
...) that don't exist here because nothing downstream reads them.

Timeframe keying: tv_bridge's Pine scripts send timeframe.period verbatim
("1", "3", "5", "15" for standard minute charts -- confirmed against real
data), and tv_scraper self-detects the same format off the chart. So
read_zone/read_atr below take the same int-minutes argument algo_v2/main.py
already uses and just str() it, keeping main.py's call sites identical to
algo_v2's.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from v3.tradingview_bot.atr_store import AtrStore, TVAtrState
from v3.tradingview_bot.zone_store import TVZone, ZoneStore

_alert_zones: Optional[ZoneStore] = None
_alert_atr: Optional[AtrStore] = None
_scraper_zones: Optional[ZoneStore] = None
_scraper_atr: Optional[AtrStore] = None


def configure(alert_zone_file: str, alert_atr_file: str,
              scraper_zone_file: str, scraper_atr_file: str) -> None:
    """Must be called once at startup, before the first read_zone/read_atr
    call -- see main.py. All four stores are read-only from here;
    tradingview_bot.main and tv_scraper.scraper (two separate processes)
    are the only writers, one pair of files each."""
    global _alert_zones, _alert_atr, _scraper_zones, _scraper_atr
    _alert_zones = ZoneStore(alert_zone_file)
    _alert_atr = AtrStore(alert_atr_file)
    _scraper_zones = ZoneStore(scraper_zone_file)
    _scraper_atr = AtrStore(scraper_atr_file)


@dataclass(frozen=True)
class Zone:
    high: float
    low: float
    virgin: bool
    start_time: int
    detected_time: int
    detected_price: float
    # Wall-clock time this zone was first observed retested, or None if
    # still virgin -- see tv_scraper/retest_tracker.py. Not used by
    # zone.py/candidates.py/management.py (ported from algo_v2 unchanged,
    # which only ever check `.virgin`), but exposed for anything that
    # wants "how long has this been retested" specifically.
    retested_at: Optional[int]
    # When this zone was fully mitigated (LuxAlgo's own array-removal),
    # distinct from retested_at -- a zone can be retested well before it's
    # ever fully mitigated. None while still active/visible on the chart.
    # Used by event_tracker.py to tell a retest-only transition apart from
    # a full mitigation.
    mitigated_time: Optional[int]


@dataclass(frozen=True)
class OBSnapshot:
    bull: list  # list[Zone], newest first
    bear: list  # list[Zone], newest first


@dataclass(frozen=True)
class ATRSnapshot:
    trend: int   # 1 = Strong (close above trail), -1 = Weak (close below trail)
    event_time: int  # bar time of the most recent Strong<->Weak flip


def _to_zone(z: TVZone) -> Zone:
    # top/btm are the same rectangle-bounds concept as ob_bridge's high/low
    # (not direction-dependent labels) -- see ZoneStore's own docstring.
    return Zone(
        high=z.top,
        low=z.btm,
        virgin=z.virgin,
        start_time=z.start_time,
        detected_time=z.detected_time,
        detected_price=z.detected_price,
        retested_at=z.retested_at,
        mitigated_time=z.mitigated_time,
    )


def _merge_tv_zone(a: TVZone, b: TVZone) -> TVZone:
    """Only matters for the rare case both sources independently assigned
    the exact same start_time. Mitigation is monotonic -- a zone mitigated
    according to EITHER source stays mitigated, even if the other source
    hasn't (or structurally can't -- alerts have no way to re-report an
    old zone at all) caught up yet. Retest is the same shape of monotonic
    fact, so retested_at is combined the same way (whichever source has it,
    or the earlier of the two if both do -- the earlier is closer to the
    true first-observed moment). For the remaining scalar fields, prefer
    whichever source detected more recently."""
    virgin = a.virgin and b.virgin
    if virgin:
        mitigated_time = None
        mitigated_price = None
    else:
        mit_source = a if not a.virgin else b
        mitigated_time = mit_source.mitigated_time
        mitigated_price = mit_source.mitigated_price

    if a.retested_at is None:
        retested_at = b.retested_at
    elif b.retested_at is None:
        retested_at = a.retested_at
    else:
        retested_at = min(a.retested_at, b.retested_at)

    newer = a if a.detected_time >= b.detected_time else b
    return TVZone(
        start_time=newer.start_time, top=newer.top, btm=newer.btm, avg=newer.avg,
        detected_time=newer.detected_time, detected_price=newer.detected_price,
        virgin=virgin, mitigated_time=mitigated_time, mitigated_price=mitigated_price,
        retested_at=retested_at,
    )


def _merged_zones(symbol: str, tf: str, direction: str) -> list[TVZone]:
    by_start_time: dict[int, TVZone] = {}
    for store in (_alert_zones, _scraper_zones):
        if store is None:
            continue
        for z in store.zones(symbol, tf, direction):
            existing = by_start_time.get(z.start_time)
            by_start_time[z.start_time] = z if existing is None else _merge_tv_zone(existing, z)
    return sorted(by_start_time.values(), key=lambda z: -z.start_time)


def _freshest_atr(a: Optional[TVAtrState], b: Optional[TVAtrState]) -> Optional[TVAtrState]:
    if a is None:
        return b
    if b is None:
        return a
    return a if a.received_at >= b.received_at else b


def read_zone(symbol: str, tf_minutes: int) -> Optional[OBSnapshot]:
    # Reload both stores first -- each was constructed once in configure()
    # and otherwise never picks up anything tv_scraper/tradingview_bot.main
    # (the actual writers) save after that (see ZoneStore.reload()'s own
    # docstring for the bug this fixes: a long-running reader silently
    # froze at whatever the files contained at startup, forever).
    if _alert_zones is not None:
        _alert_zones.reload()
    if _scraper_zones is not None:
        _scraper_zones.reload()

    tf = str(tf_minutes)
    bull = [_to_zone(z) for z in _merged_zones(symbol, tf, "bull")]
    bear = [_to_zone(z) for z in _merged_zones(symbol, tf, "bear")]
    if not bull and not bear:
        return None
    return OBSnapshot(bull=bull, bear=bear)


def read_atr(symbol: str, tf_minutes: int) -> Optional[ATRSnapshot]:
    if _alert_atr is not None:
        _alert_atr.reload()
    if _scraper_atr is not None:
        _scraper_atr.reload()

    tf = str(tf_minutes)
    a = _alert_atr.get(symbol, tf) if _alert_atr else None
    b = _scraper_atr.get(symbol, tf) if _scraper_atr else None
    state = _freshest_atr(a, b)
    if state is None:
        return None
    return ATRSnapshot(trend=state.trend, event_time=state.event_time or 0)
