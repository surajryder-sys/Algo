"""Keeps a real, persistently logged-in TradingView session open and polls
its Data Window panel for the current ATR-trail and OB-zone state -- a pull
alternative to tv_bridge/tradingview_bot's alert/webhook path.

Run with: python -m tv_scraper.scraper

First run: no saved login exists yet, so a VISIBLE browser window opens and
this process waits for you to log into TradingView in it (open the chart
manually if it doesn't load, log in, then press Enter here). The session is
saved into TV_SCRAPER_PROFILE_DIR and reused on every future run -- you only
log in once.
"""
from __future__ import annotations

import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from playwright.sync_api import Browser, Page, sync_playwright

from v3.tradingview_bot.atr_store import AtrStore
from v3.tradingview_bot.zone_store import TVZone, ZoneStore
from v3.tv_scraper.atr_trend_tracker import AtrTrendTracker
from v3.tv_scraper.config import Config, load_config
from v3.tv_scraper.first_seen_store import FirstSeenStore
from v3.tv_scraper.live_snapshot_store import LiveSnapshotStore
from v3.tv_scraper.mitigation_track_store import MitigationTrackStore
from v3.tv_scraper.parser import parse_data_window
from v3.tv_scraper.retest_tracker import RetestTracker
from v3.tv_scraper import zone_history_log

_DATA_WINDOW_TAB = "Data window"

# Plausible price range per symbol -- a zone whose top/btm falls outside
# its OWN labeled symbol's range here is rejected outright rather than
# written to the store. Added 2026-08-20 after a real, confirmed live
# incident: USOIL and USTEC share ONE browser window/process (one 2-row
# grid, not separate windows like every other symbol here), and a
# pane-focus/repaint race let USOIL's own M15 zone (top=85.88, btm=85.52
# -- USOIL's own price scale) get written under USTEC's H1 key instead,
# roughly 2.5 hours after USOIL's own real sighting of it. Fired a real
# Trend Manager BUY on USTEC off a "bullish H1 OB" that was never a real
# USTEC signal at all. formed_time_confirmed/the mitigation-debounce
# guards don't catch this -- the zone was perfectly well-formed, just
# mislabeled to a symbol whose real price is nowhere near it. Ranges are
# deliberately wide (generous headroom for real price movement) but
# don't overlap between any two symbols, so a genuine cross-symbol mixup
# can never slip through by coincidence. XAUUSD/BTCUSD/ETHUSD each have
# their own dedicated window/process (no contamination risk between
# them), but are checked too as cheap defense-in-depth.
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
    restored browsing session auto-navigating elsewhere on launch (seen live
    twice now, to two different in.tradingview.com URLs) -- that only ever
    happens once per browser startup, so a short retry clears it."""
    for attempt in range(1, attempts + 1):
        try:
            page.goto(url)
            return
        except Exception as exc:
            if attempt == attempts:
                raise
            print(f"[tv_scraper] goto interrupted (attempt {attempt}/{attempts}), retrying: {exc}")
            time.sleep(2)


def _is_logged_in(page: Page) -> bool:
    return "Logged in as" in page.content()


def _ensure_logged_in(page: Page, chart_url: str) -> None:
    """"Logged in as ..." only reliably appears in the chart page's own
    toolbar, not the plain homepage -- checking on the homepage was a false
    negative every time, forcing an unnecessary manual-login wait (during
    which the browser has been observed closing itself, likely an update or
    crash-recovery prompt) even when the real profile was already logged in.
    Check on the chart page itself, and only fall back to a manual-login
    round trip through the homepage if that genuinely fails."""
    if _is_logged_in(page):
        return

    print("[tv_scraper] Not logged in. Log into TradingView in the opened "
          "browser window, then come back here.")
    _goto_resilient(page, "https://www.tradingview.com/")
    page.wait_for_load_state("load")
    input("[tv_scraper] Press Enter once you're logged in... ")

    _goto_resilient(page, chart_url)
    page.wait_for_load_state("load")
    time.sleep(3)


def _chart_content_width(page: Page, window_width: float) -> float:
    """The Data Window/Object Tree sidebar takes a roughly FIXED PIXEL
    width, not a fraction of the window -- confirmed live: grid math based
    on window.innerWidth alone worked fine at a ~2147px-wide window (the
    sidebar was a small enough fraction of that to not matter), but broke
    as soon as the window was pinned narrower (~1720px, to fit half a
    monitor) -- the SAME sidebar pixel width now ate a bigger fraction of
    the total, so the rightmost column's click fraction (0.875) overshot
    past the actual chart content into the sidebar itself, landing on
    whatever pane was already focused instead of changing it (the
    persistent, not-self-healing duplicate-pane symptom this fixes).
    Detects the sidebar's real left edge from the "Data window" tab's own
    bounding box (same element _panel_scroll_point already locates) and
    uses THAT as the chart's true usable width; falls back to the full
    window width if the tab can't be found (e.g. mid-navigation)."""
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
    inside the given cell's center to make that pane active -- coordinates,
    not legend text, since panes can show the same timeframe label
    (ambiguous to search for) and symbols get changed manually during
    testing anyway. page.viewport_size is None under no_viewport=True
    (maximized window), so read the real size from the page itself.

    x_fraction is applied against the CHART's own content width (see
    _chart_content_width), not the full window width -- the sidebar isn't
    part of the grid."""
    size = page.evaluate("({width: window.innerWidth, height: window.innerHeight})")
    chart_width = _chart_content_width(page, size["width"])
    page.mouse.click(chart_width * x_fraction, size["height"] * y_fraction)


def _grid_panes(rows: int, cols: int) -> list[tuple[str, float, float]]:
    """Center point of every cell in a rows x cols grid, as
    (label, x_fraction, y_fraction) -- e.g. 2x2 gives r0c0 (top-left) ...
    r1c1 (bottom-right). A 1x2 grid reproduces the original left/right
    split exactly (x_fractions 0.25/0.75, single y_fraction 0.5)."""
    return [
        (f"r{r}c{c}", (c + 0.5) / cols, (r + 0.5) / rows)
        for r in range(rows) for c in range(cols)
    ]


def _panel_scroll_point(page: Page) -> Optional[tuple[float, float]]:
    """A point guaranteed to be inside the Data Window panel body, derived
    from the real position of the "Data window" tab -- a hardcoded pixel
    coordinate was landing on the chart itself instead of the sidebar
    whenever the actual window size didn't match what was assumed, which
    made the mouse wheel zoom/pan the chart uncontrollably (confirmed live:
    "chart keeps getting pushed back, squeezing the candles") instead of
    scrolling the panel."""
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

    steps has a history of silently falling behind row count as this
    indicator's own Data Window plots grew (was dropped to 8 when
    OBD_SecretTrader went from 8 zones/side back down to 4, missing the
    FormedBarsAgo/RetestedBarsAgo plots added right after; confirmed live
    that miss made _apply_direction fall back to wall-clock approximation
    for every zone, indistinguishable from a real bug without specifically
    checking for the missing fields). Back at 16, matching the row count
    of the CURRENT indicator build (Top/Btm/Retested/FormedMinutesRef/
    RetestedMinutesRef x 4 slots x 2 directions = 40 rows for this
    indicator alone) -- a FormedDay/FormedMinute/RetestedDay/
    RetestedMinute split briefly existed here (56 rows, needing steps=24),
    but got collapsed back to one combined field per formed/retest when it
    pushed the indicator's total plot count over Pine's hard 64-per-script
    ceiling (confirmed live: "RE10140 ... too many plots (66)")."""
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
            # Already the active tab -- skip the click. Clicking anyway can
            # time out for up to 30s if some transient overlay/dialog (a
            # loading spinner, an announcement popup) happens to be
            # covering it at that instant, for a click that wasn't even
            # necessary.
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
    zone's top/btm don't change while it's active, and two distinct zones
    sharing an identical top to 3dp is vanishingly unlikely in practice).
    Used only to look up this zone's real, persisted first-seen timestamp
    in FirstSeenStore -- see that module's docstring for why this price key
    itself is never written anywhere downstream as a start_time."""
    return int(round(zone["top"] * 1000))


# A zone's price_key must be missing this many CONSECUTIVE polls before
# it's treated as mitigated -- see _apply_direction's own docstring for why
# a single missing poll isn't proof enough.
_MITIGATION_DEBOUNCE_POLLS = 2

# 2025-01-01 00:00 UTC as a Unix timestamp -- the same fixed reference
# point OBD_SecretTrader.pine's _REF_EPOCH_MS uses (there as milliseconds,
# here as seconds). Both sides must agree on this exact instant for
# _reconstruct_hint() below to produce the real timestamp a Pine
# FormedMinutesRef/RetestedMinutesRef value actually means.
_REF_EPOCH_UTC = 1735689600


def _reconstruct_hint(minutes_since_ref: Optional[int]) -> Optional[int]:
    """Turns minutes-since-_REF_EPOCH_UTC -- OBD_SecretTrader.pine's
    FormedMinutesRef or RetestedMinutesRef plot -- into a real Unix
    timestamp. None if missing (na this poll, or an indicator build that
    predates this plot).

    Unlike the earlier elapsed-seconds approach (`now - seconds_ago`,
    subtracting from tv_scraper's OWN wall clock), this needs no live
    input from this process at all -- minutes_since_ref is the bar's own
    real, fixed calendar position, so the SAME real zone reconstructs to
    the EXACT same value on every single poll, forever. No rounding, no
    jitter, no tolerance window needed for matching -- see
    _find_resurrectable's own comment on why that used to be a real
    liability (a tolerance window is exactly what let two unrelated
    zones' reconstructed times collide and get merged).

    (A separate days-since-reference / minutes-since-midnight pair briefly
    existed here instead of this one combined value, but pushed
    OBD_SecretTrader.pine's total Data Window plot count over Pine's hard
    64-per-script ceiling -- confirmed live: "RE10140 ... too many plots
    (66)". One value is exactly as safe to plot -- see that Pine
    function's own comment -- and halves the row count.)"""
    if minutes_since_ref is None:
        return None
    return _REF_EPOCH_UTC + minutes_since_ref * 60


def _find_resurrectable(zone_store: ZoneStore, symbol: str, timeframe: str, direction: str,
                         formed_hint: Optional[int], top: float, btm: float) -> Optional[TVZone]:
    """Looks for an existing, still-live ZoneStore entry at the SAME price
    (top/btm, matching _price_key's own tolerance) whose own start_time
    EXACTLY matches this poll's freshly-reconstructed formed_hint. Returns
    that entry, or None.

    ZoneStore.apply_mitigated() now DELETES a zone on confirmed mitigation
    (product decision: the store holds current state, not history -- see
    that method's own docstring for the trade-off), so this can only ever
    resurrect a zone that's still sitting in the store un-mitigated -- e.g.
    one that's mid-debounce (missing 1 poll, not yet the 2 required to
    count as mitigated). A zone that already crossed the mitigation
    threshold and got deleted, then later reappeared, mints a fresh
    identity instead of recovering its old one.

    Exact match, not a tolerance window -- formed_hint is now a
    deterministic reconstruction (see _reconstruct_hint's own docstring),
    not a live approximation, so the SAME real zone reconstructs to the
    identical value every time; there's nothing left for a tolerance
    window to usefully absorb. An EARLIER version of this function used a
    60-second tolerance (needed back when hints were seconds-ago-derived
    and could drift a little poll to poll), and that tolerance was
    confirmed live to be a real liability, not just unnecessary: two
    completely unrelated real zones (62866.36 and 61745.45) happened to
    reconstruct formed_hints within that same 60-second window, and
    matching by proximity resurrected the WRONG one -- silently merging a
    brand-new zone's identity into a totally different zone's history.

    top/btm are still REQUIRED (not just a historical leftover from that
    fix) -- exact time match alone still isn't identity: two different
    real zones COULD legitimately form on the exact same bar. Requiring
    price too is what actually makes "the same real zone reappearing" a
    safe conclusion.

    This is what lets a zone that got falsely read as mitigated (pure
    top-4 Data Window display churn -- newer zones pushing it out of the
    visible slots, not a real LuxAlgo invalidation) reappear under its
    OWN original identity instead of minting a duplicate with a fresh
    start_time and a blank retest history."""
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
                      close_price: Optional[float], zone_history_log_path: Optional[str] = None
                      ) -> tuple[dict[int, int], dict[int, int], dict[int, int], dict[int, int]]:
    """Applies formed zones for one direction and marks any zone that has
    dropped out of view for _MITIGATION_DEBOUNCE_POLLS consecutive polls as
    mitigated -- LuxAlgo only removes a zone once it's actually violated, so
    "genuinely, persistently gone" here means mitigated (we just don't get
    its exact mitigation price/time, only that it happened by this poll).

    A SINGLE missing poll is not treated as mitigation, only counted --
    confirmed live: a zone still clearly visible on the actual chart (never
    left LuxAlgo's real top-4 array) got falsely mitigated, then falsely
    "reformed" as a brand-new zone with a fresh start_time on the very next
    poll, discarding its real retest history in the process. Root cause was
    a transient scrape miss (Data Window not yet repainted, a pane read
    landing wrong) for that one poll, not an actual removal. Requiring 2
    consecutive misses before declaring mitigation costs one extra poll's
    delay (~5-10s) on GENUINE mitigations, in exchange for not fabricating
    false zone churn out of ordinary scrape flakiness.

    previously_seen / the first return value: {price_key: start_time
    actually written to ZoneStore} -- price_key alone (as the old
    set-of-ints used to hold) isn't enough to mitigate correctly once
    start_time is a separately tracked, persisted value rather than being
    derived from price_key on the spot. A price_key currently mid-debounce
    (missing, but not yet for long enough) is kept in this dict too, so it
    isn't lost while its streak is still building.

    missing_streak / the second return value: {price_key: consecutive
    missed-poll count}, cleared for any price_key seen again this poll.

    detected_price is the live Close read off the Data Window this same
    poll, NOT the zone's own opposite edge -- algo_v2_tv_xauusd's M3/M5
    entry math (ported from algo_v2/entries.py) reads this as "how far has
    price already run from the OB edge," which is only ever non-negative
    if it's a genuine market price. Falls back to the zone's own edge only
    if Close wasn't parsed this poll (keeps prior, structurally-broken
    behavior rather than writing no zone at all).

    virgin: whether this zone has been RETESTED yet -- see retest_tracker.py.
    Confirmed live: a zone can sit untouched in the array for a long time
    (still virgin, still not mitigated) and then have price dip back into
    its range without yet triggering LuxAlgo's own full-mitigation removal
    -- that's a retest, and must flip virgin False immediately, not wait
    for the zone to disappear from view entirely.

    zone["retested"], when present, is Pine's OWN wick-based check (see
    OBD_SecretTrader.pine's mark_retests()) -- authoritative over this
    module's live-Close approximation, since it sees every bar's real
    high/low instead of whatever Close happened to read at poll time.
    Combined additively (never downgrades an already-True status from
    either source) rather than simply preferred, so upgrading the Pine
    script doesn't regress zones this module's own check already caught.

    start_time / retested_at: reconstructed from Pine's FormedMinutesRef/
    RetestedMinutesRef Data Window plots when available (see
    _reconstruct_hint), giving the real candle time
    instead of "whenever this module happened to first poll it." Falls
    back to plain wall-clock "first observed now" whenever a hint can't
    be computed (indicator not updated on this pane yet, na this poll, or
    an indicator build old enough to only have the earlier elapsed-
    seconds or bar-count fields).

    Two earlier approaches led here, each fixing a real problem the last
    one had: bar-COUNT fields (FormedBarsAgo/RetestedBarsAgo,
    reconstructed as `now - bars_ago * timeframe_seconds` using
    tv_scraper's OWN wall clock for BOTH halves) silently drifted by
    however much the underlying price FEED was delayed (confirmed live: a
    Pepperstone BTC CFD feed a few minutes behind the real market), since
    bar_index/bars_ago is anchored to the feed's own lagging notion of
    "now". Elapsed-SECONDS fields (FormedSecondsAgo/RetestedSecondsAgo,
    `now - seconds_ago`, Pine computing the seconds itself from its own
    timenow) fixed the feed-delay problem, but still needed tv_scraper's
    own wall clock for HALF the subtraction -- introducing a few seconds
    of poll-to-poll jitter from ordinary timing skew, needing a rounding
    step and a tolerance window to absorb. That tolerance window turned
    out to be a real liability of its own -- see _find_resurrectable's own
    comment for the confirmed-live case where it merged two unrelated
    zones' identities. The current day/minute fields need NO input from
    tv_scraper's own clock at all -- the SAME real zone reconstructs to
    the identical value on every single poll, by construction, so
    matching can be exact instead of tolerance-based.

    A hint drives FIVE pieces of behavior, in order of how much trust
    they place in it:
      1. Resurrection (_find_resurrectable): on a zone's first sighting,
         its formed_hint is checked against every zone this store already
         knows about (active or mitigated) for an EXACT start_time AND
         price match. A match means this "new" sighting is really a zone
         that got falsely marked mitigated (pure top-4 display churn, not
         a genuine LuxAlgo invalidation) reappearing -- its real
         start_time and retested_at are restored instead of minting a
         duplicate identity with a blank history.
      2. The row-corruption consistency guard: on every LATER sighting of
         an already-known zone, this poll's formed_hint is compared
         against that zone's own already-persisted start_time. ANY
         disagreement means this poll's Data Window row for this zone was
         probably caught mid-reshuffle (a scroll glitch) -- both
         formed_minutes_ref and retested_minutes_ref are distrusted for
         this poll only, falling back to plain wall-clock/close-based
         behavior rather than let corrupted numbers get
         recorded.
      3. The 2-poll retest confirmation gate (pending_retest): a NEW
         retested_hint isn't trusted until the exact same value appears on
         two consecutive polls -- guards against a row-level scroll
         glitch corrupting just the retest fields for one poll
         independently of the formation fields (so neither of the above
         two checks alone would catch it). Still needed even though hints
         are now deterministic -- this protects against a DIFFERENT
         failure (a bad single-poll read), not clock jitter.
      4. Correcting an ALREADY-recorded retested_at (RetestTracker.mark's
         force=True): confirmed live -- a value cached before this
         session's switch to real calendar-based Pine timestamps stayed
         stuck (e.g. showing a retest 5 minutes later than the chart's
         own dot), because nothing ever re-checked an already-set value
         against fresh hints. A confirmed_retest_hint that DISAGREES with
         what's already recorded goes through the SAME 2-poll
         confirmation gate as a first-time recording before it's trusted
         enough to overwrite -- one poll's disagreement could itself be
         the corrupted read, not proof the cached value is wrong.
      5. Correcting an ALREADY-recorded start_time (FirstSeenStore.restore()
         + ZoneStore.rekey()): the same class of bug as #4, but for
         formation time instead of retest time -- confirmed live on a
         rapidly top-4-churning zone (mitigated and reappeared within 26
         seconds): its own valid formed_hint happened to read na on the
         exact poll it reappeared, locking in a wall-clock-fallback
         start_time with nothing to ever re-check it afterward. Same
         2-poll confirmation gate (pending_formed) before a disagreeing
         formed_hint is trusted enough to overwrite the cached start_time
         -- and since ZoneStore is keyed BY start_time, the correction
         also moves that entry to its new key (see ZoneStore.rekey()'s own
         docstring) rather than leaving an orphaned duplicate behind.
         Some zones (old enough that Pine's [] operator's 10000-bar hard
         ceiling makes formed_hint permanently unavailable -- see
         bar_day_minute()'s own comment) can never be corrected this way
         at all; that's a known, accepted gap for very old zones, not
         something this mechanism claims to solve.
      6. Orphan reconciliation (added 2026-08-22): a ZoneStore entry with
         no matching previously_seen record at all -- see the loop right
         at the top of this function's own body for the confirmed live
         case and full rationale. Seeded into previously_seen so it's no
         longer permanently invisible to the missing-streak mitigation
         check below."""
    price_field = "btm" if direction == "bull" else "top"
    now = int(time.time())

    # Reconcile ZoneStore entries that have no corresponding
    # previously_seen record at all -- confirmed live 2026-08-22: a
    # BTCUSD H4 bear zone from 2026-01-31 (202 days old) was still
    # sitting in the store marked virgin, with a COMPLETELY EMPTY
    # missing_streak dict for its whole bucket, even though price had
    # since traded straight through its range on the way to 79500 (which
    # should have mitigated it long ago). The missing-streak loop below
    # can only ever act on a price_key already present in previously_seen
    # (it iterates previously_seen.keys() - seen_now.keys()) -- a zone
    # whose price_key was NEVER in previously_seen is therefore
    # permanently invisible to mitigation detection, no matter what price
    # does. Root cause of how the two stores (ZoneStore's own file vs
    # MitigationTrackStore's) got out of sync isn't fully provable after
    # the fact (most likely some zone_store write -- a resurrection, a
    # rekey, a merge -- that wasn't mirrored into the tracker), but this
    # closes the gap regardless of how it happened: seed the orphan into
    # previously_seen as if it were "last seen" one poll ago, and the
    # SAME existing 2-poll debounce below naturally catches and deletes
    # it over the next two polls if it's genuinely no longer visible --
    # no separate mechanism, no risk of double-deleting a zone that IS
    # still legitimately live (this only ever adds a missing tracking
    # entry, never removes one).
    for stored_zone in zones.zones(symbol, timeframe, direction):
        orphan_key = _price_key({"top": stored_zone.top})
        if orphan_key not in previously_seen:
            previously_seen[orphan_key] = stored_zone.start_time
            print(f"[tv_scraper] {symbol} {timeframe} {direction}: reconciled orphaned zone "
                  f"start_time={stored_zone.start_time} top={stored_zone.top} btm={stored_zone.btm} "
                  f"virgin={stored_zone.virgin} -- existed in ZoneStore with no mitigation-tracking "
                  f"entry, now eligible for normal missing-streak mitigation")

    seen_now: dict[int, int] = {}
    new_missing_streak: dict[int, int] = {}
    new_pending_retest: dict[int, int] = {}
    new_pending_formed: dict[int, int] = {}

    for zone in current:
        if not _price_plausible(symbol, zone["top"], zone["btm"]):
            # Cross-symbol contamination guard -- see _SYMBOL_PRICE_RANGE's
            # own comment. Deliberately doesn't touch seen_now/previously_seen
            # at all: this poll simply didn't see anything usable in this
            # slot, same as if the read had failed outright, rather than
            # treating a real zone as freshly missing/mitigated over a single
            # bad poll.
            print(f"[tv_scraper] {symbol} {timeframe} {direction}: REJECTED implausible zone "
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
                # Disagreement could be THIS poll's row corrupted (the
                # common case, see the comment above) -- but it could also
                # mean the CACHED start_time itself is wrong, e.g. a
                # rapidly top-4-churning zone that got a wall-clock
                # fallback locked in on a poll where its own formed_hint
                # happened to read na. Only correct once the SAME
                # disagreeing hint is confirmed on two consecutive polls
                # -- same protection a first-time recording already gets,
                # see this function's own docstring point 5.
                if pending_formed.get(price_key) == formed_hint:
                    # rekey() first, and only follow through on the
                    # cache/start_time update if it actually succeeded --
                    # see its own docstring for the confirmed-live
                    # collision this guards (formed_hint landing on a
                    # minute a COMPLETELY DIFFERENT real zone already
                    # occupies, which would otherwise silently merge two
                    # zones' histories into one). Leaving pending_formed
                    # populated (not clearing it here) means a refused
                    # correction keeps retrying every poll instead of
                    # silently giving up.
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
        # live-Close approximation -- see RetestTracker.reconcile()'s own
        # docstring for the confirmed-live case this fixes. Deliberately
        # BEFORE check()/mark() below, so this poll's read already
        # reflects the corrected state instead of the stale one.
        pine_retested_flag = zone.get("retested")
        if pine_retested_flag is not None:
            retested.reconcile(symbol, timeframe, direction, price_key, pine_retested_flag)

        retested_at = retested.check(symbol, timeframe, direction, price_key,
                                     close_price, zone["btm"], zone["top"], is_first_sighting)

        # retested_hint: same reconstruction as formed_hint, but for the
        # retest bar -- distrusted entirely this poll if the consistency
        # guard above already flagged this row as corrupted (the same
        # scroll glitch that corrupts one field on a row very often
        # corrupts the others too).
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
            # Something's already cached (retested_at above just returned
            # it unchanged) -- see if a fresh, 2-poll-confirmed hint
            # disagrees. Confirmed live: a value cached before this
            # session's switch from bar-count to seconds-based Pine
            # timestamps stayed stuck 5 minutes off the chart's own dot,
            # since nothing ever re-checked an already-set value against
            # newer hints. Only ever correct via a hint that's ALREADY
            # passed the same 2-poll confirmation a first-time recording
            # needs -- see RetestTracker.mark()'s own force= docstring.
            if confirmed_retest_hint is not None and confirmed_retest_hint != already_recorded:
                retested_at = retested.mark(symbol, timeframe, direction, price_key,
                                             hint=confirmed_retest_hint, force=True)
        # Same first-sighting guard as the Close-based check() above --
        # Pine's own "Retested" Data Window plot is keyed by ARRAY SLOT
        # INDEX (Bull1-4/Bear1-4), not a stable zone identity (see
        # OBD_SecretTrader.pine's plot section). When LuxAlgo's array
        # shifts -- an old zone removed, a new one taking over that same
        # slot number -- the new zone can inherit the old one's
        # Retested=1 flag for one poll before price has ever touched it.
        # Confirmed live: a zone with start_time == retested_at to the
        # exact second (retested at the literal instant it was first
        # observed). Skipping this on the first-sighting poll costs at
        # most one poll's delay before a GENUINE same-bar retest is
        # caught (mark_retests() keeps setting the flag every tick until
        # this module next polls), which is a fine trade for not
        # fabricating retests out of slot reuse.
        elif retested_at is None and pine_retested_flag and not is_first_sighting:
            retested_at = retested.mark(symbol, timeframe, direction, price_key,
                                         hint=confirmed_retest_hint)

        # Log to the permanent zone-history record the FIRST time this
        # exact start_time is ever written to ZoneStore -- not every
        # poll's re-confirmation of an already-known zone. ZoneStore
        # itself deletes on mitigation, so this is the only place this
        # zone's range/times survive past that. See zone_history_log.py's
        # own docstring for why this exists.
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
            # See ZoneStore.TVZone.formed_time_confirmed's own docstring
            # -- False whenever formed_hint is na this poll (Pine's
            # 10000-bar [] ceiling exceeded), meaning start_time is a
            # wall-clock guess, not this zone's real formation time.
            "formed_time_confirmed": formed_hint is not None,
        })

    for price_key in previously_seen.keys() - seen_now.keys():
        streak = missing_streak.get(price_key, 0) + 1
        if streak < _MITIGATION_DEBOUNCE_POLLS:
            # Not yet confirmed gone -- carry it forward as still "seen" so
            # a transient miss doesn't lose the zone's identity/start_time,
            # but track the streak so a genuine mitigation isn't delayed
            # past the next poll that also misses it.
            seen_now[price_key] = previously_seen[price_key]
            new_missing_streak[price_key] = streak
            continue

        zones.apply_mitigated(symbol, timeframe, direction, {
            "start_time": previously_seen[price_key],
            "mitigated_time": now,
            "mitigated_price": None,
        })
        first_seen.forget(symbol, timeframe, direction, price_key)
        retested.forget(symbol, timeframe, direction, price_key)
        # No corresponding pending_retest cleanup needed -- new_pending_retest
        # is rebuilt from scratch every poll (only ever populated for
        # price_keys seen THIS poll), so a mitigated zone's stale pending
        # hint simply isn't carried forward.

    return seen_now, new_missing_streak, new_pending_retest, new_pending_formed


# Keyed by pane_label -- the last (symbol, timeframe) this pane
# successfully processed. See run_once_pane's own comment on the
# symbol-switch guard this enables. Deliberately still in-process-only
# (not persisted like MitigationTrackStore below) -- resetting this on
# restart only costs one extra "skip this poll" per pane the first time
# it's read after a restart, harmless, unlike the mitigation-tracking
# state this used to sit next to.
_last_symbol_tf: dict[str, tuple[str, str]] = {}


def _zone_signature(zones_list: list[dict]) -> tuple:
    """A comparable snapshot of a parsed zone list's own top/btm values,
    in order -- used by the settle-verification check below to confirm
    two consecutive Data Window reads actually agree on the PLOTTED
    VALUES, not just the pane's header text."""
    return tuple((z.get("top"), z.get("btm")) for z in zones_list)


def _parsed_values_agree(a, b) -> bool:
    """True only if two ParsedState reads agree on bull/bear zone
    top/btm, ATR trail_stop, AND Close -- see run_once_pane's own
    comment for why this checks the VALUES, not just symbol/timeframe.
    Close was missed in the original 2026-08-22 version of this check
    (only zones/ATR were compared) -- confirmed live the same day: an
    ETHUSD M3 pane read a wildly implausible Close (~1916 against a real
    ~2420) that still passed, because the two consecutive reads
    apparently agreed on zones/ATR while the stale/wrong Close value was
    consistent across both of them. Close feeds Trend/Reversal Manager's
    own entry/distance math directly (via live_snapshot_store), so it
    needs the exact same settle-guarantee as the zone and ATR data."""
    a_atr = (a.atr or {}).get("trail_stop")
    b_atr = (b.atr or {}).get("trail_stop")
    return (_zone_signature(a.bull_zones) == _zone_signature(b.bull_zones)
            and _zone_signature(a.bear_zones) == _zone_signature(b.bear_zones)
            and a_atr == b_atr
            and a.close == b.close)


def run_once_pane(page: Page, zones: ZoneStore, atr: AtrStore, first_seen: FirstSeenStore,
                   retested: RetestTracker, trend_tracker: AtrTrendTracker, live: LiveSnapshotStore,
                   mitigation_track: MitigationTrackStore,
                   pane_label: str, x_fraction: float, y_fraction: float, configured_symbol: str,
                   configured_timeframe: str, zone_history_log_path: Optional[str] = None) -> None:
    _focus_pane(page, x_fraction, y_fraction)
    # The Data Window sidebar doesn't repaint for the newly-focused pane
    # instantly -- reading it right after the click (as this used to do)
    # sometimes captured the PREVIOUSLY focused pane's still-displayed data
    # instead, confirmed live: two panes read back as identical for
    # several consecutive polls when cycling through a 4-pane grid. A
    # short wait for TradingView to actually repaint fixes it -- same
    # settle-time pattern already used after the very first focus in
    # main(), just applied every cycle instead of only once.
    time.sleep(0.4)
    _open_data_window(page)
    text = _collect_data_window_text(page)
    parsed = parse_data_window(text)

    if parsed.atr is None and not parsed.bull_zones and not parsed.bear_zones:
        print(f"[tv_scraper][{pane_label}][DEBUG] captured {len(text)} chars | "
              f"'Order Block' in text: {'Order Block' in text} | "
              f"'Bull1' in text: {'Bull1' in text}")
        # Data Window tab may have reverted to Object Tree, or the click
        # above landed wrong -- self-heal by reopening and re-reading once
        # before giving up on this pane for this cycle.
        _open_data_window(page)
        text = _collect_data_window_text(page)
        parsed = parse_data_window(text)

    if parsed.symbol is None:
        # Nothing to detect at all -- this pane has no symbol loaded (e.g.
        # "This symbol doesn't exist" after clearing it), not just a
        # transient scrape miss. Falling back to the configured symbol here
        # would silently write fake data under the wrong label; skip this
        # pane for this cycle instead.
        print(f"[tv_scraper][{pane_label}] no symbol detected (pane empty?) -- skipping")
        return

    now = time.time()
    symbol = parsed.symbol
    timeframe = parsed.timeframe or configured_timeframe

    # A pane's symbol/timeframe changing since last poll means someone
    # just manually switched it on the actual chart -- skip processing
    # THIS one poll rather than risk a transition-window read. Confirmed
    # live (BTCUSD/M3 + M1 dual-pane): right after switching a pane's
    # symbol on TradingView, one poll landed with the Data Window HEADER
    # already showing the new symbol but the PLOTTED zone prices still
    # reflecting the old symbol (Pine's plots hadn't recomputed yet),
    # writing XAUUSD-scale prices (~4370) under a "BTCUSD" key. The
    # existing missing-streak debounce eventually cleans up the resulting
    # bogus entries (they vanish next poll and get marked mitigated), but
    # by then they're already sitting in the permanent history. Skipping
    # the one transition poll avoids writing it at all -- costs one
    # poll's delay (~5s) on a deliberate, infrequent user action.
    last_symbol_tf = _last_symbol_tf.get(pane_label)
    _last_symbol_tf[pane_label] = (symbol, timeframe)
    if last_symbol_tf is not None and last_symbol_tf != (symbol, timeframe):
        print(f"[tv_scraper][{pane_label}] symbol/timeframe changed "
              f"({last_symbol_tf[0]}/{last_symbol_tf[1]} -> {symbol}/{timeframe}) "
              f"-- skipping this poll to let the chart settle")
        return

    # Verify this read is genuinely settled, not just its header text --
    # added 2026-08-22 after a real live incident: a real M5 XAUUSD zone's
    # own top/btm values got written under BOTH M30's and M15's own
    # timeframe keys, even though each pane's HEADER correctly reported
    # "M30"/"M15" the whole time -- the check above never caught it,
    # because it only compares the header (symbol, timeframe), never the
    # actual plotted Bull/Bear values. Those are rendered in a separate
    # part of the page and can lag behind the header after the 0.4s
    # settle sleep above. A second, cheap (no-scroll, since Bull1-4/
    # Bear1-4 top/btm are always in the first screenful) read a short
    # moment later that must AGREE with the first is a direct check on
    # the data itself, not a proxy that can be right while the values
    # underneath are still stale from whichever pane was focused before
    # this one.
    time.sleep(0.3)
    confirm_text = page.evaluate("document.body.innerText")
    confirm_parsed = parse_data_window(confirm_text)
    if not _parsed_values_agree(parsed, confirm_parsed):
        print(f"[tv_scraper][{pane_label}] {symbol} {timeframe}: zone/ATR values still settling "
              f"(two reads disagreed) -- skipping this poll")
        return

    # Same cross-symbol contamination risk as OB zones (see
    # _SYMBOL_PRICE_RANGE's own comment) applies to Close and the ATR
    # trail-stop too -- both are raw prices read off the same pane. A
    # contaminated Close would also corrupt entry/distance math
    # downstream (live.apply below feeds Trend/Reversal Manager's own
    # _read_live_close), so this is checked before anything uses it,
    # not just before writing zones.
    if parsed.close is not None and not _price_plausible(symbol, parsed.close, parsed.close):
        print(f"[tv_scraper] {symbol} {timeframe}: REJECTED implausible Close={parsed.close} "
              f"-- outside {symbol}'s own price range, likely cross-symbol contamination")
        parsed.close = None

    atr_data = None
    if parsed.atr is not None and _price_plausible(symbol, parsed.atr["trail_stop"], parsed.atr["trail_stop"]):
        atr_data = dict(parsed.atr)
        # trend: derived live from close vs trail_stop instead of a
        # dedicated chart plot (see atr_trend_tracker.py's docstring for
        # why -- a "Trend" plot broke this chart's price autoscale the
        # same way raw start_time once did for OB zones). Only overrides
        # the parser's own trend value when Close was actually read this
        # poll; falls back to whatever the parser found otherwise.
        if parsed.close is not None:
            computed_trend = 1 if parsed.close > atr_data["trail_stop"] else -1
            trend, event_time = trend_tracker.update(symbol, timeframe, computed_trend, int(now))
            atr_data["trend"] = trend
            atr_data["event_time"] = event_time
        atr.apply(symbol, timeframe, atr_data, now)
    elif parsed.atr is not None:
        print(f"[tv_scraper] {symbol} {timeframe}: REJECTED implausible ATR trail_stop="
              f"{parsed.atr['trail_stop']} -- outside {symbol}'s own price range, "
              f"likely cross-symbol contamination")

    for direction, direction_zones in (("bull", parsed.bull_zones), ("bear", parsed.bear_zones)):
        seen, streak, pending_retest, pending_formed = _apply_direction(
            zones, first_seen, retested, symbol, timeframe, direction, direction_zones,
            mitigation_track.get_last_seen(symbol, timeframe, direction),
            mitigation_track.get_missing_streak(symbol, timeframe, direction),
            mitigation_track.get_pending_retest(symbol, timeframe, direction),
            mitigation_track.get_pending_formed(symbol, timeframe, direction), parsed.close,
            zone_history_log_path)
        mitigation_track.update(symbol, timeframe, direction, seen, streak, pending_retest, pending_formed)

    # Raw mirror -- exactly this poll's parsed Bull1-4/Bear1-4 (top/btm/
    # retested) and Close/ATR, no history, no interpretation. See
    # live_snapshot_store.py's own docstring for why this exists
    # separately from the zones/atr stores above.
    live.apply(symbol, timeframe, parsed.close, atr_data, parsed.bull_zones, parsed.bear_zones, now)

    print(f"[tv_scraper][{pane_label}] symbol={symbol} tf={timeframe} atr={atr_data} "
          f"close={parsed.close} bull={len(parsed.bull_zones)} bear={len(parsed.bear_zones)}")


def run_once(page: Page, zones: ZoneStore, atr: AtrStore, first_seen: FirstSeenStore,
             retested: RetestTracker, trend_tracker: AtrTrendTracker, live: LiveSnapshotStore,
             mitigation_track: MitigationTrackStore,
             symbol: str, timeframe: str, panes: list[tuple[str, float, float]],
             zone_history_log_path: Optional[str] = None) -> None:
    for pane_label, x_fraction, y_fraction in panes:
        run_once_pane(page, zones, atr, first_seen, retested, trend_tracker, live, mitigation_track,
                      pane_label, x_fraction, y_fraction, symbol, timeframe, zone_history_log_path)


# Anti-throttling flags shared by both the CDP-launch path (below) and the
# old launch_persistent_context call this replaced -- Windows-native
# window-occlusion detection (CalculateNativeWinOcclusion) throttles the
# whole renderer process when the window is minimized/covered, confirmed
# live to freeze data updates solid even with the page-JS visibility
# override (see main()) also in place. Chromium only honors the LAST
# --disable-features on the command line, so this repeats Playwright's own
# default list (as observed in its launch command) instead of silently
# wiping it out.
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
    like a real DevTools endpoint -- checked BEFORE trying
    playwright.connect_over_cdp() so a launch-and-wait only happens when
    genuinely nothing is there yet, not on every single restart."""
    try:
        with urllib.request.urlopen(f"{_cdp_endpoint(port)}/json/version", timeout=2):
            return True
    except (urllib.error.URLError, OSError):
        return False


def _launch_browser_for_cdp(cfg: Config) -> None:
    """Starts Brave as a plain OS subprocess (not through Playwright) with
    remote debugging enabled, then returns immediately -- main() polls
    _cdp_port_open() afterward and connects once it's actually up. A plain
    subprocess, not launch_persistent_context, specifically so this
    process doesn't hold an exclusive Playwright-side handle on the
    browser: once launched this way, ANY later tv_scraper run (or the user
    manually, or another tool) can attach to the SAME instance over CDP
    instead of fighting over the profile lock -- see config.py's own
    comment on cdp_port for the "profile already in use" crashes this
    replaces."""
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
    """Attaches over CDP to an already-running Brave if one's listening on
    cfg.cdp_port; otherwise launches one (see _launch_browser_for_cdp) and
    waits for it to come up. Either way, the result is a SHARED browser --
    unlike the old launch_persistent_context call this replaced, nothing
    here exclusively locks the profile, so the same window can be looked
    at / interacted with directly, and restarting this script for a code
    change no longer requires closing and reopening the browser."""
    if not _cdp_port_open(cfg.cdp_port):
        print(f"[tv_scraper] no browser on CDP port {cfg.cdp_port} -- launching one")
        _launch_browser_for_cdp(cfg)
        for _ in range(30):
            if _cdp_port_open(cfg.cdp_port):
                break
            time.sleep(1)
        else:
            raise RuntimeError(
                f"Browser didn't come up on CDP port {cfg.cdp_port} after 30s -- "
                f"check TV_SCRAPER_BROWSER_PATH points at a real Brave/Chrome install."
            )
    else:
        print(f"[tv_scraper] attaching to existing browser on CDP port {cfg.cdp_port}")
    return p.chromium.connect_over_cdp(_cdp_endpoint(cfg.cdp_port))


def main() -> None:
    cfg = load_config()
    zones = ZoneStore(cfg.zone_state_file)
    atr = AtrStore(cfg.atr_state_file)
    first_seen = FirstSeenStore(cfg.first_seen_state_file)
    retested = RetestTracker(cfg.retest_state_file)
    trend_tracker = AtrTrendTracker(cfg.trend_state_file)
    live = LiveSnapshotStore(cfg.live_snapshot_file)
    mitigation_track = MitigationTrackStore(cfg.mitigation_track_file)

    with sync_playwright() as p:
        browser = _connect_browser(p, cfg)
        # A CDP-attached browser launched with --user-data-dir already has
        # one default context (there's no separate "create the context"
        # step the way launch_persistent_context did it) -- attach to
        # that, or create one in the unlikely event none exists yet.
        context = browser.contexts[0] if browser.contexts else browser.new_context()

        # TradingView's own JS throttles/pauses live updates when it thinks
        # the page is backgrounded (Page Visibility API), independent of any
        # Chromium-level anti-throttling flags -- confirmed live: data froze
        # solid for 8+ minutes while this window sat behind others. Override
        # the visibility API so the page always believes it's in the
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
        # spawning a new one -- confirmed live: repeated tv_scraper
        # restarts (many times over, this session alone) each left behind
        # an orphaned tab whenever the process was force-killed rather
        # than shut down cleanly (a force-kill skips the `finally:
        # page.close()` below entirely, since Python cleanup code never
        # runs on SIGKILL), piling up duplicate tabs of the same chart.
        # Checking first means a restart reattaches to its own leftover
        # tab instead of adding another. Prefix match (not exact
        # equality) because TradingView can append query params/hashes
        # after navigation that a fresh cfg.chart_url string won't have.
        # Other tabs may legitimately belong to the user or another tool
        # -- this only ever touches one matching THIS chart's URL, never
        # closes anything outside of it.
        chart_prefix = cfg.chart_url.rstrip("/")
        page = next((pg for pg in context.pages if pg.url.rstrip("/").startswith(chart_prefix)), None)
        if page is None:
            page = context.new_page()

        # Go straight to the chart -- if this profile is already logged in
        # (the common case, reusing your real browser profile), this is the
        # only navigation needed at all. "networkidle" is unusable here and
        # below: TradingView's live price feed keeps a permanent websocket
        # connection open, so the page never goes network-idle and this wait
        # can hang indefinitely. "load" plus a short fixed pause is reliable
        # instead. If not logged in, TradingView shows "Chart not found" for
        # this private layout (privacy behavior, not an error) --
        # _ensure_logged_in handles that case via the homepage.
        _goto_resilient(page, cfg.chart_url)
        page.wait_for_load_state("load")
        time.sleep(3)  # let the chart + indicators actually render
        _ensure_logged_in(page, cfg.chart_url)
        panes = _grid_panes(cfg.grid_rows, cfg.grid_cols)
        print(f"[tv_scraper] grid {cfg.grid_rows}x{cfg.grid_cols} -> panes: "
              f"{[label for label, _, _ in panes]}")
        _focus_pane(page, *panes[0][1:])
        _open_data_window(page)
        time.sleep(2)  # let the panel render once before the first poll

        print(f"[tv_scraper] polling every {cfg.poll_seconds}s -- Ctrl+C to stop")
        try:
            while True:
                try:
                    run_once(page, zones, atr, first_seen, retested, trend_tracker, live, mitigation_track,
                             cfg.symbol, cfg.timeframe, panes, cfg.zone_history_log_file)
                except Exception as exc:
                    print(f"[tv_scraper] ERROR: {exc}")
                time.sleep(cfg.poll_seconds)
        except KeyboardInterrupt:
            pass
        finally:
            # Close only OUR tab, never the shared browser/context -- see
            # the "work from our own dedicated new tab" comment above.
            try:
                page.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
