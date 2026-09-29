---
name: project-tv-scraper-window-maximized
description: "tv_scraper's Brave browser window must stay maximized -- user has 4 monitors with different resolutions and moves it between them"
metadata: 
  node_type: memory
  type: project
  originSessionId: beca6970-4dba-4bca-8c27-e6b6f4892269
  modified: 2026-08-16T18:30:27.200Z
---

The user has 4 monitors, each a different resolution, and periodically
moves the Brave browser window `v3/tv_scraper/scraper.py` reads (via CDP
attach on port 9222) between them. Since `tv_scraper` *attaches* to an
already-running browser instead of launching fresh, it never re-applies
`.env`'s `TV_SCRAPER_WINDOW_WIDTH`/`HEIGHT` — it just uses whatever the
window's real current OS size happens to be. Moving between differently
sized monitors (or a sleep/wake or display reconfiguration) can shrink
the actual window well below what the pane-grid click math
(`_focus_pane`/`_chart_content_width` in scraper.py) was sized for.

**Why this matters:** confirmed live (2026-08-16) that a shrunk window
(1288x982, down from the configured 3440x1440) caused the two rightmost
panes in a 4-column grid (r0c3, r1c3) to silently duplicate their
left-neighbor's data (r0c2, r1c2) every poll — the columns became too
cramped for reliable click-targeting near the sidebar boundary. No
error was logged; symbol/tf labels looked plausible, just wrong pane.
User confirmed they hadn't touched the window — the resize came from
moving it between monitors earlier. See [[project_tv_scraper_chart_timezone]]
for the related IST-display-offset gotcha on the same setup.

**How to apply:** the user has committed to keeping this window
**maximized** going forward. If pane readings ever look suspicious again
(e.g. two panes reading identical data, especially adjacent ones in the
same row), check actual window size first before assuming a code bug —
a quick read-only Playwright script connecting via
`connect_over_cdp("http://localhost:9222")` and reading
`window.innerWidth/innerHeight` on the chart page (with the scraper
process stopped first, to avoid interference) confirms it directly.
