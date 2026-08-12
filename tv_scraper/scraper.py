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

import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, sync_playwright

from tradingview_bot.atr_store import AtrStore
from tradingview_bot.zone_store import ZoneStore
from tv_scraper.atr_trend_tracker import AtrTrendTracker
from tv_scraper.config import Config, load_config
from tv_scraper.first_seen_store import FirstSeenStore
from tv_scraper.parser import parse_data_window
from tv_scraper.retest_tracker import RetestTracker

_DATA_WINDOW_TAB = "Data window"


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


def _focus_pane(page: Page, x_fraction: float, y_fraction: float) -> None:
    """The layout has multiple chart panes in a grid; the sidebar panels
    (Data Window included) reflect whichever pane last had focus. Click
    inside the given cell's center to make that pane active -- coordinates,
    not legend text, since panes can show the same timeframe label
    (ambiguous to search for) and symbols get changed manually during
    testing anyway. page.viewport_size is None under no_viewport=True
    (maximized window), so read the real size from the page itself."""
    size = page.evaluate("({width: window.innerWidth, height: window.innerHeight})")
    page.mouse.click(size["width"] * x_fraction, size["height"] * y_fraction)


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


def _collect_data_window_text(page: Page, steps: int = 8) -> str:
    """The Data Window is a scrollable list that TradingView virtualizes --
    rows outside the current scroll position aren't in the DOM at all, so a
    single innerText read can silently miss most indicators. Scrolls through
    the panel capturing text at each position and concatenates everything;
    re-parsing the same label twice is harmless."""
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


def _apply_direction(zones: ZoneStore, first_seen: FirstSeenStore, retested: RetestTracker,
                      symbol: str, timeframe: str, direction: str, current: list[dict],
                      previously_seen: dict[int, int], close_price: Optional[float]) -> dict[int, int]:
    """Applies formed zones for one direction and marks any zone that was
    visible last poll but has now dropped out of view as mitigated -- LuxAlgo
    only removes a zone once it's actually violated, so "disappeared" here
    means mitigated (we just don't get its exact mitigation price/time, only
    that it happened by this poll).

    previously_seen / the return value: {price_key: start_time actually
    written to ZoneStore} -- price_key alone (as the old set-of-ints used to
    hold) isn't enough to mitigate correctly once start_time is a separately
    tracked, persisted value rather than being derived from price_key
    on the spot.

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

    retested_at: wall-clock time this module first observed the retest
    (from either source) -- not the true retest bar's own time, same
    "first observed, not true origin" tradeoff start_time already makes
    (see first_seen_store.py/retest_tracker.py's module docstring for why
    a raw Pine bar_time can't be plotted here directly)."""
    price_field = "btm" if direction == "bull" else "top"
    now = int(time.time())
    seen_now: dict[int, int] = {}

    for zone in current:
        price_key = _price_key(zone)
        start_time = first_seen.get_or_create(symbol, timeframe, direction, price_key)
        seen_now[price_key] = start_time
        is_first_sighting = (start_time == now)
        retested_at = retested.check(symbol, timeframe, direction, price_key,
                                     close_price, zone["btm"], zone["top"], is_first_sighting)
        if retested_at is None and zone.get("retested"):
            retested_at = retested.mark(symbol, timeframe, direction, price_key)

        zones.apply_formed(symbol, timeframe, direction, {
            "start_time": start_time,
            "top": zone["top"],
            "btm": zone["btm"],
            "avg": (zone["top"] + zone["btm"]) / 2,
            "detected_time": now,
            "detected_price": close_price if close_price is not None else zone[price_field],
            "virgin": retested_at is None,
            "retested_at": retested_at,
        })

    for price_key in previously_seen.keys() - seen_now.keys():
        zones.apply_mitigated(symbol, timeframe, direction, {
            "start_time": previously_seen[price_key],
            "mitigated_time": now,
            "mitigated_price": None,
        })
        first_seen.forget(symbol, timeframe, direction, price_key)
        retested.forget(symbol, timeframe, direction, price_key)

    return seen_now


# Keyed by (symbol, timeframe, direction) rather than just pane label --
# every pane in the grid gets polled every cycle and can show different
# symbols/timeframes (especially during weekend testing with manually-
# switched symbols), so mitigation-detection must never mix one pane's
# zones into another's "previously seen" set. Value is {price_key:
# start_time} -- see _apply_direction's docstring for why both are needed.
_last_seen: dict[tuple[str, str, str], dict[int, int]] = {}


def run_once_pane(page: Page, zones: ZoneStore, atr: AtrStore, first_seen: FirstSeenStore,
                   retested: RetestTracker, trend_tracker: AtrTrendTracker, pane_label: str,
                   x_fraction: float, y_fraction: float, configured_symbol: str,
                   configured_timeframe: str) -> None:
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

    atr_data = None
    if parsed.atr is not None:
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

    for direction, direction_zones in (("bull", parsed.bull_zones), ("bear", parsed.bear_zones)):
        cache_key = (symbol, timeframe, direction)
        _last_seen[cache_key] = _apply_direction(
            zones, first_seen, retested, symbol, timeframe, direction, direction_zones,
            _last_seen.get(cache_key, {}), parsed.close)

    print(f"[tv_scraper][{pane_label}] symbol={symbol} tf={timeframe} atr={atr_data} "
          f"close={parsed.close} bull={len(parsed.bull_zones)} bear={len(parsed.bear_zones)}")


def run_once(page: Page, zones: ZoneStore, atr: AtrStore, first_seen: FirstSeenStore,
             retested: RetestTracker, trend_tracker: AtrTrendTracker, symbol: str, timeframe: str,
             panes: list[tuple[str, float, float]]) -> None:
    for pane_label, x_fraction, y_fraction in panes:
        run_once_pane(page, zones, atr, first_seen, retested, trend_tracker, pane_label,
                      x_fraction, y_fraction, symbol, timeframe)


def main() -> None:
    cfg = load_config()
    zones = ZoneStore(cfg.zone_state_file)
    atr = AtrStore(cfg.atr_state_file)
    first_seen = FirstSeenStore(cfg.first_seen_state_file)
    retested = RetestTracker(cfg.retest_state_file)
    trend_tracker = AtrTrendTracker(cfg.trend_state_file)
    profile_dir = Path(cfg.profile_dir).resolve()

    with sync_playwright() as p:
        launch_kwargs = {}
        if cfg.browser_executable_path:
            launch_kwargs["executable_path"] = cfg.browser_executable_path

        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            no_viewport=True,  # let the window (below) dictate content size --
            args=[
                "--start-maximized",  # a fixed viewport would fight this
                # Confirmed live: minimizing the window froze data updates
                # even with the page-JS visibility override below in place.
                # Windows-native window-occlusion detection throttles the
                # whole renderer process below the JS layer when a window is
                # minimized/covered -- CalculateNativeWinOcclusion is the
                # actual Chromium flag controlling that. Chromium only
                # honors the LAST --disable-features on the command line, so
                # this repeats Playwright's own default list (as observed in
                # its launch command) instead of silently wiping it out.
                "--disable-features=AvoidUnnecessaryBeforeUnloadCheckSync,"
                "BoundaryEventDispatchTracksNodeRemoval,DestroyProfileOnBrowserClose,"
                "DialMediaRouteProvider,GlobalMediaControls,HttpsUpgrades,LensOverlay,"
                "MediaRouter,PaintHolding,ThirdPartyStoragePartitioning,"
                "BlockOriginHeaderModificationOnRedirect,Translate,AutoDeElevate,"
                "OptimizationHints,msForceBrowserSignIn,"
                "msEdgeUpdateLaunchServicesPreferredVersion,CalculateNativeWinOcclusion",
            ],
            **launch_kwargs,
        )

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

        # This real profile's restored browsing session can open several
        # tabs (confirmed live: garbage zone data from what looks like BTC
        # and other unrelated symbols got mixed into our XAUUSD output,
        # because whichever tab happened to be active got scraped). Always
        # work from one dedicated fresh tab, and close every other one so
        # there's never ambiguity about which page is being read.
        page = context.new_page()
        for other in list(context.pages):
            if other is not page:
                try:
                    other.close()
                except Exception:
                    pass

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
                    run_once(page, zones, atr, first_seen, retested, trend_tracker,
                             cfg.symbol, cfg.timeframe, panes)
                except Exception as exc:
                    print(f"[tv_scraper] ERROR: {exc}")
                time.sleep(cfg.poll_seconds)
        except KeyboardInterrupt:
            pass
        finally:
            context.close()


if __name__ == "__main__":
    main()
