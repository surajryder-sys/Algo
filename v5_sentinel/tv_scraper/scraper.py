"""Keeps a real, persistently logged-in TradingView session open and polls
its Data Window panel for the current OB-zone state -- ported 2026-09-07
from v3/tv_scraper/scraper.py at the user's explicit direction ("everything
is same, even the chart url everything is same, just port or copy from
there and add it in v5"). Feeds V5-Sentinel's second Reversal Manager
component (OB-zone based, alongside the existing STR/ATR-trail one).

Run with: python -m v5_sentinel.tv_scraper.scraper

First run: no saved login exists yet, so a VISIBLE browser window opens and
this process waits for you to log into TradingView in it (open the chart
manually if it doesn't load, log in, then press Enter here). The session is
saved into V5S_TV_SCRAPER_PROFILE_DIR and reused on every future run -- you
only log in once. Runs as its OWN independent browser instance (own CDP
port, own profile dir) so it can run alongside v3's tv_scraper without
either fighting over the same browser lock.

DELIBERATELY NARROWER than v3's scraper: this only tracks OB zone state
(formed/retested/top/btm) -- no ATR-trail reading, no AtrStore, no
AtrTrendTracker. "MT5 data is only for execution, ATR based" (user,
2026-09-07): M3/M1 LTF confirmation and SL basis stay exactly as STR
already built them (bridge-only), unchanged by this component. This
scraper's only job is supplying the OB zone side of the picture.

Everything else -- the pane-focus/settle-verification/stuck-pane-detection
machinery, the full zone lifecycle tracking (formation, retest, mitigation
debounce, resurrection, orphan reconciliation) -- is carried over verbatim
from v3's already-proven, battle-tested implementation. See each imported
module's own docstring for the specific live incidents that shaped it;
none of that history is repeated here.
"""
from __future__ import annotations

import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from playwright.sync_api import Browser, Page, sync_playwright

from v5_sentinel.tv_scraper.config import Config, load_config
from v5_sentinel.tv_scraper.first_seen_store import FirstSeenStore
from v5_sentinel.tv_scraper.live_snapshot_store import LiveSnapshotStore
from v5_sentinel.tv_scraper.mitigation_track_store import MitigationTrackStore
from v5_sentinel.tv_scraper.parser import parse_data_window
from v5_sentinel.tv_scraper.retest_tracker import RetestTracker
from v5_sentinel.tv_scraper import zone_history_log
from v5_sentinel.tv_scraper.zone_store import TVZone, ZoneStore

_DATA_WINDOW_TAB = "Data window"

# Plausible price range per symbol -- a zone whose top/btm falls outside its
# OWN labeled symbol's range here is rejected outright rather than written
# to the store. Carried over from v3/tv_scraper/scraper.py's own
# cross-symbol contamination guard -- kept even though this scraper is
# XAUUSD-only for now, since it costs nothing and the same class of
# pane-focus/repaint race that caused that module's confirmed live incident
# could in principle happen here too if this grid is ever widened to more
# symbols.
_SYMBOL_PRICE_RANGE = {
    "XAUUSD": (1000.0, 10000.0),
    "BTCUSD": (10000.0, 300000.0),
    "ETHUSD": (300.0, 20000.0),
    "USOIL": (5.0, 500.0),
    "USTEC": (3000.0, 100000.0),
}


def _price_plausible(symbol: str, top: float, btm: float) -> bool:
    bounds = _SYMBOL_PRICE_RANGE.get(symbol)
    if bounds is None:
        return True  # no configured range for this symbol -- nothing to check against
    lo, hi = bounds
    return lo <= btm and top <= hi


def _goto_resilient(page: Page, url: str, attempts: int = 4) -> None:
    """page.goto can get raced by a leftover tab from this real profile's
    restored browsing session auto-navigating elsewhere on launch -- that
    only ever happens once per browser startup, so a short retry clears it."""
    for attempt in range(1, attempts + 1):
        try:
            page.goto(url)
            return
        except Exception as exc:
            if attempt == attempts:
                raise
            print(f"[V5S-TVZ] goto interrupted (attempt {attempt}/{attempts}), retrying: {exc}")
            time.sleep(2)


def _is_logged_in(page: Page) -> bool:
    return "Logged in as" in page.content()


def _ensure_logged_in(page: Page, chart_url: str) -> None:
    """"Logged in as ..." only reliably appears in the chart page's own
    toolbar, not the plain homepage -- checking on the homepage is a false
    negative. Check on the chart page itself, and only fall back to a
    manual-login round trip through the homepage if that genuinely fails."""
    if _is_logged_in(page):
        return

    print("[V5S-TVZ] Not logged in. Log into TradingView in the opened "
          "browser window, then come back here.")
    _goto_resilient(page, "https://www.tradingview.com/")
    page.wait_for_load_state("load")
    input("[V5S-TVZ] Press Enter once you're logged in... ")

    _goto_resilient(page, chart_url)
    page.wait_for_load_state("load")
    time.sleep(3)


def _chart_content_width(page: Page, window_width: float) -> float:
    """The Data Window/Object Tree sidebar takes a roughly FIXED PIXEL
    width, not a fraction of the window. Detects the sidebar's real left
    edge from the "Data window" tab's own bounding box and uses THAT as the
    chart's true usable width; falls back to the full window width if the
    tab can't be found (e.g. mid-navigation)."""
    tab = page.get_by_role("tab", name=_DATA_WINDOW_TAB, exact=True)
    if tab.count() == 0:
        tab = page.get_by_text(_DATA_WINDOW_TAB, exact=True)
    if tab.count() == 0:
        return window_width
    box = tab.first.bounding_box()
    if box is None or box["x"] <= 0:
        return window_width
    return box["x"]


def _focus_pane(page: Page, x_fraction: float, y_fraction: float) -> None:
    """The layout has multiple chart panes in a grid; the sidebar panels
    (Data Window included) reflect whichever pane last had focus. Click
    inside the given cell's center to make that pane active.

    x_fraction is applied against the CHART's own content width (see
    _chart_content_width), not the full window width -- the sidebar isn't
    part of the grid."""
    size = page.evaluate("({width: window.innerWidth, height: window.innerHeight})")
    chart_width = _chart_content_width(page, size["width"])
    page.mouse.click(chart_width * x_fraction, size["height"] * y_fraction)


def _grid_panes(rows: int, cols: int) -> list[tuple[str, float, float]]:
    """Center point of every cell in a rows x cols grid, as
    (label, x_fraction, y_fraction) -- e.g. 6x1 gives r0c0 (top) ...
    r5c0 (bottom)."""
    return [
        (f"r{r}c{c}", (c + 0.5) / cols, (r + 0.5) / rows)
        for r in range(rows) for c in range(cols)
    ]


def _panel_scroll_point(page: Page) -> Optional[tuple[float, float]]:
    """A point guaranteed to be inside the Data Window panel body, derived
    from the real position of the "Data window" tab -- a hardcoded pixel
    coordinate risks landing on the chart itself instead of the sidebar,
    zooming/panning the chart instead of scrolling the panel."""
    tab = page.get_by_role("tab", name=_DATA_WINDOW_TAB, exact=True)
    if tab.count() == 0:
        tab = page.get_by_text(_DATA_WINDOW_TAB, exact=True)
    if tab.count() == 0:
        return None
    box = tab.first.bounding_box()
    if box is None:
        return None
    return (box["x"] + box["width"] / 2, box["y"] + box["height"] + 100)


def _collect_data_window_text(page: Page, steps: int = 16) -> str:
    """The Data Window is a scrollable list that TradingView virtualizes --
    rows outside the current scroll position aren't in the DOM at all, so a
    single innerText read can silently miss most indicators. Scrolls through
    the panel capturing text at each position and concatenates everything;
    re-parsing the same label twice is harmless.

    steps=16 matches the row count of the current OBD_SecretTrader.pine
    build (Top/Btm/Retested/FormedMinutesRef/RetestedMinutesRef x 4 slots x
    2 directions = 40 rows) -- see v3/tv_scraper/scraper.py's own comment
    for the full history of why this number matters."""
    point = _panel_scroll_point(page)
    if point is None:
        # Can't safely locate the panel -- read whatever's there right now
        # rather than risk scrolling (and zooming) the chart instead.
        return page.evaluate("document.body.innerText")

    page.mouse.move(*point)
    page.mouse.wheel(0, -10000)  # back to top first
    time.sleep(0.2)

    chunks = [page.evaluate("document.body.innerText")]
    for _ in range(steps):
        page.mouse.wheel(0, 300)
        time.sleep(0.2)
        chunks.append(page.evaluate("document.body.innerText"))
    return "\n".join(chunks)


def _open_data_window(page: Page) -> None:
    for locator in (
        page.get_by_role("tab", name=_DATA_WINDOW_TAB, exact=True),
        page.get_by_text(_DATA_WINDOW_TAB, exact=True),
        page.get_by_role("button", name=_DATA_WINDOW_TAB),
    ):
        if locator.count() > 0:
            el = locator.first
            # Already the active tab -- skip the click.
            if el.get_attribute("aria-selected") == "true":
                return
            try:
                el.click(timeout=5000)
            except Exception:
                pass  # best-effort -- a poll cycle can self-heal via run_once_pane
            return
    raise RuntimeError(f"Could not find the '{_DATA_WINDOW_TAB}' tab on the page")


def _price_key(zone: dict) -> int:
    """A stable-enough LOOKUP key derived from the zone's top price (a
    zone's top/btm don't change while it's active). Used only to look up
    this zone's real, persisted first-seen timestamp in FirstSeenStore --
    never written anywhere downstream as a start_time."""
    return int(round(zone["top"] * 1000))


# A zone's price_key must be missing this many CONSECUTIVE polls before
# it's treated as mitigated -- see _apply_direction's own docstring for why
# a single missing poll isn't proof enough.
_MITIGATION_DEBOUNCE_POLLS = 2

# 2025-01-01 00:00 UTC as a Unix timestamp -- the same fixed reference point
# OBD_SecretTrader.pine's _REF_EPOCH_MS uses (there as milliseconds, here as
# seconds). Both sides must agree on this exact instant for
# _reconstruct_hint() below to produce the real timestamp a Pine
# FormedMinutesRef/RetestedMinutesRef value actually means.
_REF_EPOCH_UTC = 1735689600


def _reconstruct_hint(minutes_since_ref: Optional[int]) -> Optional[int]:
    """Turns minutes-since-_REF_EPOCH_UTC -- OBD_SecretTrader.pine's
    FormedMinutesRef or RetestedMinutesRef plot -- into a real Unix
    timestamp. None if missing (na this poll, or an indicator build that
    predates this plot). The bar's own real, fixed calendar position, so
    the SAME real zone reconstructs to the EXACT same value on every poll,
    forever -- no rounding, no jitter, no tolerance window needed."""
    if minutes_since_ref is None:
        return None
    return _REF_EPOCH_UTC + minutes_since_ref * 60


def _find_resurrectable(zone_store: ZoneStore, symbol: str, timeframe: str, direction: str,
                         formed_hint: Optional[int], top: float, btm: float) -> Optional[TVZone]:
    """Looks for an existing, still-live ZoneStore entry at the SAME price
    (top/btm) whose own start_time EXACTLY matches this poll's
    freshly-reconstructed formed_hint. Returns that entry, or None.

    Lets a zone that got falsely read as mitigated (pure top-4 Data Window
    display churn, not a genuine LuxAlgo invalidation) reappear under its
    OWN original identity instead of minting a duplicate with a fresh
    start_time and a blank retest history. Exact match, not a tolerance
    window -- see v3/tv_scraper/scraper.py's own docstring for the
    confirmed-live collision a tolerance window used to cause here."""
    if formed_hint is None:
        return None
    for z in zone_store.zones(symbol, timeframe, direction):
        if z.start_time == formed_hint and abs(z.top - top) <= 0.01 and abs(z.btm - btm) <= 0.01:
            return z
    return None


def _apply_direction(zones: ZoneStore, first_seen: FirstSeenStore, retested: RetestTracker,
                      symbol: str, timeframe: str, direction: str, current: list[dict],
                      previously_seen: dict[int, int], missing_streak: dict[int, int],
                      pending_retest: dict[int, int], pending_formed: dict[int, int],
                      pending_pine_confirm: dict[int, int],
                      close_price: Optional[float], zone_history_log_path: Optional[str] = None
                      ) -> tuple[dict[int, int], dict[int, int], dict[int, int], dict[int, int], dict[int, int]]:
    """Applies formed zones for one direction and marks any zone that has
    dropped out of view for _MITIGATION_DEBOUNCE_POLLS consecutive polls as
    mitigated. Ported verbatim (logic unchanged) from
    v3/tv_scraper/scraper.py -- see that module's own extensive docstring
    for the full rationale and confirmed-live incidents behind every piece
    of this: the 2-poll debounce, hint-based resurrection, the row-
    corruption consistency guard, the 2-poll retest confirmation gate,
    already-recorded-value correction, and orphan reconciliation."""
    price_field = "btm" if direction == "bull" else "top"
    now = int(time.time())

    # Reconcile ZoneStore entries that have no corresponding
    # previously_seen record at all -- see v3's own docstring for the
    # confirmed-live incident (a 202-day-old orphaned zone, immune to
    # mitigation detection because it was never in previously_seen to
    # begin with).
    current_price_keys = {_price_key(z) for z in current if _price_plausible(symbol, z["top"], z["btm"])}
    for stored_zone in zones.zones(symbol, timeframe, direction):
        orphan_key = _price_key({"top": stored_zone.top})
        if orphan_key in previously_seen:
            continue  # already tracked normally -- nothing to reconcile

        if orphan_key in current_price_keys:
            # Genuinely visible THIS poll, just never tracked before --
            # not stale, only untracked. Seed as "seen last poll" so the
            # current-poll loop below processes it completely normally.
            previously_seen[orphan_key] = stored_zone.start_time
            continue

        # Confirmed orphan: absent from previously_seen AND absent from
        # this poll's actual read -- definitively not visible, no grace
        # period needed.
        zones.apply_mitigated(symbol, timeframe, direction, {
            "start_time": stored_zone.start_time,
            "mitigated_time": now,
            "mitigated_price": None,
        })
        if zone_history_log_path is not None:
            zone_history_log.append_removed(
                zone_history_log_path, symbol=symbol, timeframe=timeframe, direction=direction,
                start_time=stored_zone.start_time, top=stored_zone.top, btm=stored_zone.btm,
                virgin=stored_zone.virgin, removed_time=now, reason="orphan")
        first_seen.forget(symbol, timeframe, direction, orphan_key)
        retested.forget(symbol, timeframe, direction, orphan_key)
        print(f"[V5S-TVZ] {symbol} {timeframe} {direction}: deleted orphaned zone "
              f"start_time={stored_zone.start_time} top={stored_zone.top} btm={stored_zone.btm} "
              f"virgin={stored_zone.virgin} -- existed in ZoneStore with no mitigation-tracking "
              f"entry and wasn't visible this poll either, no 2-poll grace period given")

    seen_now: dict[int, int] = {}
    new_missing_streak: dict[int, int] = {}
    new_pending_retest: dict[int, int] = {}
    new_pending_formed: dict[int, int] = {}
    new_pending_pine_confirm: dict[int, int] = {}

    for zone in current:
        if not _price_plausible(symbol, zone["top"], zone["btm"]):
            print(f"[V5S-TVZ] {symbol} {timeframe} {direction}: REJECTED implausible zone "
                  f"top={zone['top']} btm={zone['btm']} -- outside {symbol}'s own price range, "
                  f"likely cross-symbol contamination from another pane")
            continue

        price_key = _price_key(zone)
        is_first_sighting = price_key not in previously_seen

        formed_hint = _reconstruct_hint(zone.get("formed_minutes_ref"))

        hints_consistent = True
        if is_first_sighting:
            resurrect = _find_resurrectable(zones, symbol, timeframe, direction, formed_hint,
                                            zone["top"], zone["btm"])
            if resurrect is not None:
                start_time = resurrect.start_time
                first_seen.restore(symbol, timeframe, direction, price_key, start_time)
                retested.restore(symbol, timeframe, direction, price_key, resurrect.retested_at)
            else:
                start_time = first_seen.get_or_create(symbol, timeframe, direction, price_key,
                                                        hint=formed_hint)
        else:
            start_time = first_seen.get_or_create(symbol, timeframe, direction, price_key)
            if formed_hint is not None and formed_hint != start_time:
                hints_consistent = False
                # Disagreement could be THIS poll's row corrupted (a scroll
                # glitch), or the CACHED start_time itself could be wrong.
                # Only correct once the SAME disagreeing hint is confirmed
                # on two consecutive polls.
                if pending_formed.get(price_key) == formed_hint:
                    if zones.rekey(symbol, timeframe, direction, start_time, formed_hint):
                        first_seen.restore(symbol, timeframe, direction, price_key, formed_hint)
                        start_time = formed_hint
                        hints_consistent = True
                    else:
                        new_pending_formed[price_key] = formed_hint
                else:
                    new_pending_formed[price_key] = formed_hint

        seen_now[price_key] = start_time

        # Let Pine's own Retested plot (when present this poll) correct a
        # previously-recorded false positive from this module's own
        # live-Close approximation. Deliberately BEFORE check()/mark()
        # below, so this poll's read already reflects the corrected state.
        pine_retested_flag = zone.get("retested")
        if pine_retested_flag is not None:
            retested.reconcile(symbol, timeframe, direction, price_key, pine_retested_flag)

        retested_at = retested.check(symbol, timeframe, direction, price_key,
                                     close_price, zone["btm"], zone["top"], is_first_sighting)

        retested_hint: Optional[int] = None
        if hints_consistent:
            retested_hint = _reconstruct_hint(zone.get("retested_minutes_ref"))

        confirmed_retest_hint: Optional[int] = None
        if retested_hint is not None:
            if pending_retest.get(price_key) == retested_hint:
                confirmed_retest_hint = retested_hint
            else:
                new_pending_retest[price_key] = retested_hint

        already_recorded = retested.peek(symbol, timeframe, direction, price_key)
        if already_recorded is not None:
            if confirmed_retest_hint is not None and confirmed_retest_hint != already_recorded:
                retested_at = retested.mark(symbol, timeframe, direction, price_key,
                                             hint=confirmed_retest_hint, force=True)
        elif retested_at is None and pine_retested_flag and not is_first_sighting:
            # A single poll's pine_retested_flag=True for an ALREADY-
            # established zone can be a slot-reuse artifact (LuxAlgo's
            # Retested flag is keyed by ARRAY SLOT, not zone identity) --
            # only trust it once it reads True on two polls in a row.
            if price_key in pending_pine_confirm:
                retested_at = retested.mark(symbol, timeframe, direction, price_key,
                                             hint=confirmed_retest_hint)
            else:
                new_pending_pine_confirm[price_key] = 1

        # Log to the permanent zone-history record the FIRST time this
        # exact start_time is ever written to ZoneStore.
        if zone_history_log_path is not None and zones.get(symbol, timeframe, direction, start_time) is None:
            zone_history_log.append(
                zone_history_log_path, symbol=symbol, timeframe=timeframe, direction=direction,
                start_time=start_time, top=zone["top"], btm=zone["btm"], detected_time=now,
                formed_time_confirmed=formed_hint is not None,
            )

        zones.apply_formed(symbol, timeframe, direction, {
            "start_time": start_time,
            "top": zone["top"],
            "btm": zone["btm"],
            "avg": (zone["top"] + zone["btm"]) / 2,
            "detected_time": now,
            "detected_price": close_price if close_price is not None else zone[price_field],
            "virgin": retested_at is None,
            "retested_at": retested_at,
            "formed_time_confirmed": formed_hint is not None,
        })

    for price_key in previously_seen.keys() - seen_now.keys():
        streak = missing_streak.get(price_key, 0) + 1
        if streak < _MITIGATION_DEBOUNCE_POLLS:
            seen_now[price_key] = previously_seen[price_key]
            new_missing_streak[price_key] = streak
            continue

        if zone_history_log_path is not None:
            about_to_delete = zones.get(symbol, timeframe, direction, previously_seen[price_key])
        else:
            about_to_delete = None
        zones.apply_mitigated(symbol, timeframe, direction, {
            "start_time": previously_seen[price_key],
            "mitigated_time": now,
            "mitigated_price": None,
        })
        if about_to_delete is not None:
            zone_history_log.append_removed(
                zone_history_log_path, symbol=symbol, timeframe=timeframe, direction=direction,
                start_time=about_to_delete.start_time, top=about_to_delete.top, btm=about_to_delete.btm,
                virgin=about_to_delete.virgin, removed_time=now, reason="debounced")
        first_seen.forget(symbol, timeframe, direction, price_key)
        retested.forget(symbol, timeframe, direction, price_key)

    return seen_now, new_missing_streak, new_pending_retest, new_pending_formed, new_pending_pine_confirm


# Keyed by pane_label -- the last (symbol, timeframe) this pane
# successfully processed. See run_once_pane's own comment on the
# symbol-switch guard this enables.
_last_symbol_tf: dict[str, tuple[str, str]] = {}


def _zone_signature(zones_list: list[dict]) -> tuple:
    """A comparable snapshot of a parsed zone list's own top/btm values, in
    order -- used by the settle-verification check below to confirm two
    consecutive Data Window reads actually agree on the PLOTTED VALUES, not
    just the pane's header text."""
    return tuple((z.get("top"), z.get("btm")) for z in zones_list)


def _parsed_values_agree(a, b) -> bool:
    """True only if two ParsedState reads agree on bull/bear zone top/btm
    AND Close. Deliberately does NOT compare ATR here (v3's own version
    does -- this scraper doesn't consume ATR at all, see module docstring,
    so an ATR mismatch between two reads is irrelevant to what this module
    actually writes downstream)."""
    return (_zone_signature(a.bull_zones) == _zone_signature(b.bull_zones)
            and _zone_signature(a.bear_zones) == _zone_signature(b.bear_zones)
            and a.close == b.close)


# The single most-recently-successfully-processed pane's own data signature
# -- guards against a SUSTAINED stuck-focus read (a pane's click never
# actually moving focus, so it keeps reading a neighboring pane's data
# indefinitely). See v3/tv_scraper/scraper.py's own module-level docstring
# for the two confirmed-live incidents (a fake USTEC M15 OB, a stale
# XAUUSD M5 read stuck on M15's data for over an hour) this guards against,
# and why timeframe/close are deliberately excluded from the signature.
_last_processed_pane: Optional[tuple] = None


def _pane_data_signature(symbol: str, parsed) -> tuple:
    return (symbol, _zone_signature(parsed.bull_zones), _zone_signature(parsed.bear_zones))


def run_once_pane(page: Page, zones: ZoneStore, first_seen: FirstSeenStore,
                   retested: RetestTracker, live: LiveSnapshotStore,
                   mitigation_track: MitigationTrackStore,
                   pane_label: str, x_fraction: float, y_fraction: float, configured_symbol: str,
                   configured_timeframe: str, zone_history_log_path: Optional[str] = None) -> None:
    _focus_pane(page, x_fraction, y_fraction)
    # The Data Window sidebar doesn't repaint for the newly-focused pane
    # instantly -- a short wait for TradingView to actually repaint avoids
    # capturing the PREVIOUSLY focused pane's still-displayed data.
    time.sleep(0.4)
    _open_data_window(page)
    text = _collect_data_window_text(page)
    parsed = parse_data_window(text)

    if parsed.atr is None and not parsed.bull_zones and not parsed.bear_zones:
        print(f"[V5S-TVZ][{pane_label}][DEBUG] captured {len(text)} chars | "
              f"'Order Block' in text: {'Order Block' in text} | "
              f"'Bull1' in text: {'Bull1' in text}")
        # Data Window tab may have reverted to Object Tree, or the click
        # above landed wrong -- self-heal by reopening and re-reading once
        # before giving up on this pane for this cycle.
        _open_data_window(page)
        text = _collect_data_window_text(page)
        parsed = parse_data_window(text)

    if parsed.symbol is None:
        print(f"[V5S-TVZ][{pane_label}] no symbol detected (pane empty?) -- skipping")
        return

    now = time.time()
    symbol = parsed.symbol
    timeframe = parsed.timeframe or configured_timeframe

    # A pane's symbol/timeframe changing since last poll means someone just
    # manually switched it on the actual chart -- skip processing THIS one
    # poll rather than risk a transition-window read.
    last_symbol_tf = _last_symbol_tf.get(pane_label)
    _last_symbol_tf[pane_label] = (symbol, timeframe)
    if last_symbol_tf is not None and last_symbol_tf != (symbol, timeframe):
        print(f"[V5S-TVZ][{pane_label}] symbol/timeframe changed "
              f"({last_symbol_tf[0]}/{last_symbol_tf[1]} -> {symbol}/{timeframe}) "
              f"-- skipping this poll to let the chart settle")
        return

    # Verify this read is genuinely settled, not just its header text -- a
    # second, cheap read a short moment later that must AGREE with the
    # first is a direct check on the data itself.
    time.sleep(0.3)
    confirm_text = page.evaluate("document.body.innerText")
    confirm_parsed = parse_data_window(confirm_text)
    if not _parsed_values_agree(parsed, confirm_parsed):
        print(f"[V5S-TVZ][{pane_label}] {symbol} {timeframe}: zone values still settling "
              f"(two reads disagreed) -- skipping this poll")
        return

    # Guards against a SUSTAINED stuck-focus read, not just a transient one
    # -- see _last_processed_pane's own module-level docstring.
    global _last_processed_pane
    this_signature = _pane_data_signature(symbol, parsed)
    if _last_processed_pane is not None:
        prev_label, prev_signature = _last_processed_pane
        if prev_label != pane_label and prev_signature == this_signature:
            print(f"[V5S-TVZ][{pane_label}] {symbol} {timeframe}: identical to {prev_label}'s just-read "
                  f"data -- likely stuck on the wrong pane, skipping this poll")
            return
    _last_processed_pane = (pane_label, this_signature)

    # Same cross-symbol contamination risk as OB zones applies to Close --
    # both are raw prices read off the same pane.
    if parsed.close is not None and not _price_plausible(symbol, parsed.close, parsed.close):
        print(f"[V5S-TVZ] {symbol} {timeframe}: REJECTED implausible Close={parsed.close} "
              f"-- outside {symbol}'s own price range, likely cross-symbol contamination")
        parsed.close = None

    for direction, direction_zones in (("bull", parsed.bull_zones), ("bear", parsed.bear_zones)):
        seen, streak, pending_retest, pending_formed, pending_pine_confirm = _apply_direction(
            zones, first_seen, retested, symbol, timeframe, direction, direction_zones,
            mitigation_track.get_last_seen(symbol, timeframe, direction),
            mitigation_track.get_missing_streak(symbol, timeframe, direction),
            mitigation_track.get_pending_retest(symbol, timeframe, direction),
            mitigation_track.get_pending_formed(symbol, timeframe, direction),
            mitigation_track.get_pending_pine_confirm(symbol, timeframe, direction), parsed.close,
            zone_history_log_path)
        mitigation_track.update(symbol, timeframe, direction, seen, streak, pending_retest, pending_formed,
                                 pending_pine_confirm)

    # Raw mirror -- exactly this poll's parsed Bull1-4/Bear1-4 (top/btm/
    # retested) and Close, no history, no interpretation. atr is always
    # None here -- this scraper doesn't track it, see module docstring.
    live.apply(symbol, timeframe, parsed.close, None, parsed.bull_zones, parsed.bear_zones, now)

    print(f"[V5S-TVZ][{pane_label}] symbol={symbol} tf={timeframe} "
          f"close={parsed.close} bull={len(parsed.bull_zones)} bear={len(parsed.bear_zones)}")


def run_once(page: Page, zones: ZoneStore, first_seen: FirstSeenStore,
             retested: RetestTracker, live: LiveSnapshotStore,
             mitigation_track: MitigationTrackStore,
             symbol: str, timeframe: str, panes: list[tuple[str, float, float]],
             zone_history_log_path: Optional[str] = None) -> None:
    for pane_label, x_fraction, y_fraction in panes:
        run_once_pane(page, zones, first_seen, retested, live, mitigation_track,
                      pane_label, x_fraction, y_fraction, symbol, timeframe, zone_history_log_path)


# Anti-throttling flags -- Windows-native window-occlusion detection
# (CalculateNativeWinOcclusion) throttles the whole renderer process when
# the window is minimized/covered, confirmed live (in v3's own scraper) to
# freeze data updates solid even with the page-JS visibility override (see
# main()) also in place. Chromium only honors the LAST --disable-features
# on the command line, so this repeats Playwright's own default list
# instead of silently wiping it out.
_ANTI_THROTTLE_FLAG = (
    "--disable-features=AvoidUnnecessaryBeforeUnloadCheckSync,"
    "BoundaryEventDispatchTracksNodeRemoval,DestroyProfileOnBrowserClose,"
    "DialMediaRouteProvider,GlobalMediaControls,HttpsUpgrades,LensOverlay,"
    "MediaRouter,PaintHolding,ThirdPartyStoragePartitioning,"
    "BlockOriginHeaderModificationOnRedirect,Translate,AutoDeElevate,"
    "OptimizationHints,msForceBrowserSignIn,"
    "msEdgeUpdateLaunchServicesPreferredVersion,CalculateNativeWinOcclusion"
)

_DEFAULT_BRAVE_PATH = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"


def _cdp_endpoint(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def _cdp_port_open(port: int) -> bool:
    """True if something's already listening on the CDP port and answering
    like a real DevTools endpoint."""
    try:
        with urllib.request.urlopen(f"{_cdp_endpoint(port)}/json/version", timeout=2):
            return True
    except (urllib.error.URLError, OSError):
        return False


def _launch_browser_for_cdp(cfg: Config) -> None:
    """Starts Brave as a plain OS subprocess (not through Playwright) with
    remote debugging enabled, then returns immediately -- main() polls
    _cdp_port_open() afterward and connects once it's actually up. A plain
    subprocess so this process doesn't hold an exclusive Playwright-side
    handle on the browser: any later run (or the user manually) can attach
    to the SAME instance over CDP instead of fighting over the profile lock."""
    executable = cfg.browser_executable_path or _DEFAULT_BRAVE_PATH
    profile_dir = Path(cfg.profile_dir).resolve()
    args = [
        executable,
        f"--remote-debugging-port={cfg.cdp_port}",
        f"--user-data-dir={profile_dir}",
        f"--window-position={cfg.window_x},{cfg.window_y}",
        f"--window-size={cfg.window_width},{cfg.window_height}",
        _ANTI_THROTTLE_FLAG,
        "about:blank",
    ]
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                      stdin=subprocess.DEVNULL, close_fds=True)


def _connect_browser(p, cfg: Config) -> Browser:
    """Attaches over CDP to an already-running browser if one's listening
    on cfg.cdp_port; otherwise launches one and waits for it to come up.
    Either way, the result is a SHARED browser -- nothing here exclusively
    locks the profile, so the same window can be looked at / interacted
    with directly, and restarting this script for a code change doesn't
    require closing and reopening the browser at all."""
    if not _cdp_port_open(cfg.cdp_port):
        print(f"[V5S-TVZ] no browser on CDP port {cfg.cdp_port} -- launching one")
        _launch_browser_for_cdp(cfg)
        for _ in range(30):
            if _cdp_port_open(cfg.cdp_port):
                break
            time.sleep(1)
        else:
            raise RuntimeError(
                f"Browser didn't come up on CDP port {cfg.cdp_port} after 30s -- "
                f"check V5S_TV_SCRAPER_BROWSER_PATH points at a real Brave/Chrome install."
            )
    else:
        print(f"[V5S-TVZ] attaching to existing browser on CDP port {cfg.cdp_port}")
    return p.chromium.connect_over_cdp(_cdp_endpoint(cfg.cdp_port))


def main() -> None:
    cfg = load_config()
    zones = ZoneStore(cfg.zone_state_file)
    first_seen = FirstSeenStore(cfg.first_seen_state_file)
    retested = RetestTracker(cfg.retest_state_file)
    live = LiveSnapshotStore(cfg.live_snapshot_file)
    mitigation_track = MitigationTrackStore(cfg.mitigation_track_file)

    with sync_playwright() as p:
        browser = _connect_browser(p, cfg)
        context = browser.contexts[0] if browser.contexts else browser.new_context()

        # TradingView's own JS throttles/pauses live updates when it thinks
        # the page is backgrounded (Page Visibility API), independent of
        # any Chromium-level anti-throttling flags. Override the
        # visibility API so the page always believes it's in the
        # foreground, on every navigation.
        context.add_init_script("""
            Object.defineProperty(document, 'hidden', { get: () => false, configurable: true });
            Object.defineProperty(document, 'visibilityState', { get: () => 'visible', configurable: true });
            document.hasFocus = () => true;
            for (const evt of ['visibilitychange', 'blur', 'webkitvisibilitychange']) {
                window.addEventListener(evt, (e) => e.stopImmediatePropagation(), true);
            }
        """)

        # Reuse an already-open tab for this exact chart instead of always
        # spawning a new one -- avoids piling up duplicate tabs across
        # restarts. Prefix match (not exact equality) because TradingView
        # can append query params/hashes after navigation.
        chart_prefix = cfg.chart_url.rstrip("/")
        page = next((pg for pg in context.pages if pg.url.rstrip("/").startswith(chart_prefix)), None)
        if page is None:
            page = context.new_page()

        _goto_resilient(page, cfg.chart_url)
        page.wait_for_load_state("load")
        time.sleep(3)  # let the chart + indicators actually render
        _ensure_logged_in(page, cfg.chart_url)
        panes = _grid_panes(cfg.grid_rows, cfg.grid_cols)
        print(f"[V5S-TVZ] grid {cfg.grid_rows}x{cfg.grid_cols} -> panes: "
              f"{[label for label, _, _ in panes]}")
        _focus_pane(page, *panes[0][1:])
        _open_data_window(page)
        time.sleep(2)  # let the panel render once before the first poll

        print(f"[V5S-TVZ] polling every {cfg.poll_seconds}s -- Ctrl+C to stop")
        try:
            while True:
                try:
                    run_once(page, zones, first_seen, retested, live, mitigation_track,
                             cfg.symbol, cfg.timeframe, panes, cfg.zone_history_log_file)
                except Exception as exc:
                    print(f"[V5S-TVZ] ERROR: {exc}")
                time.sleep(cfg.poll_seconds)
        except KeyboardInterrupt:
            pass
        finally:
            # Close only OUR tab, never the shared browser/context.
            try:
                page.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
