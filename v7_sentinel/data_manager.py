"""V7-Sentinel Data Manager -- consolidates tv_scraper + NLB/NSB Watcher +
ICT Block Watcher into one process, as three plain OS threads.

Built 2026-09-24 as part of the V6S -> V7S architecture consolidation
(see trade_manager_main.py's own docstring for the full incident/
rationale). Unlike Trade Manager, there's no shared-MT5-connection
benefit here -- tv_scraper never touches MT5 at all (pure Playwright/
browser automation over CDP), and NLB/NSB Watcher's own MT5 connection
was never the one that went stale in the incident this consolidation
responds to. The value here is purely fewer processes to operate.

WHY THREADS, NOT ONE UNIFIED LOOP: tv_scraper (`tv_scraper/scraper.py`)
and NLB/NSB Watcher (`nlb_nsb_watcher.py`) are both already synchronous,
blocking `while True` loops (confirmed while designing this -- no
asyncio anywhere), but they touch entirely disjoint native resources
(Playwright/CDP vs MT5's own IPC channel) and have independent polling
cadences. Two plain daemon threads, each running its OWN existing
`main()` close to verbatim, is the simplest way to combine them into one
process without forcing them into a shared loop shape neither was built
for. No cross-thread state: NLB/NSB Watcher reads tv_scraper's own zone
state file exactly like it always has, mediated by the filesystem, not
by any in-process coupling -- unaffected by which process writes it.

Each thread wraps its target `main()` in an outer restart-loop -- new
defense-in-depth versus V6S's own shape: today, an exception escaping
either process's own OUTER setup code (e.g. a Playwright browser-connect
failure before scraper.py's own inner loop even starts) kills that whole
process outright. In V7S, it should only kill and restart that ONE
thread, never take Data Manager itself down.

THIRD THREAD (2026-09-24, ict_ob_watcher -- see that module's own
docstring): drives ict_ob_block.ICTBlockStore, the merged TV+MT5 OB-zone
store RM-ICT's new MT5-zone entries and the new ICT Exit component both
read read-only. Has its OWN MT5 connection (mt5.initialize() is
idempotent, same pattern nlb_nsb_watcher already uses) -- kept a
separate thread rather than folded into nlb_nsb_watcher's own loop since
that module's job is specifically the H4-M10 NLB/NSB Block, a different
store with different scope; this keeps both single-purpose.

FOURTH THREAD (2026-09-26, major_minor_watcher -- see that module's own
docstring): recomputes and persists Major/Minor Support/Resistance per
timeframe, data-import only (no entry/exit logic reads this yet). Its
own MUCH slower cadence (~60s, vs. the 1s main poll) since a single
recompute takes ~9 seconds -- this is exactly why it's a separate
thread rather than folded into any of the other three.

Run with: python -m v7_sentinel.data_manager
"""
from __future__ import annotations

import threading
import time

from v7_sentinel import ict_ob_watcher, major_minor_watcher, nlb_nsb_watcher
from v7_sentinel.tv_scraper import scraper as tv_scraper

_RESTART_DELAY_SECONDS = 5.0


def _run_tv_scraper() -> None:
    while True:
        try:
            tv_scraper.main()
        except Exception as exc:  # noqa: BLE001 -- one thread's failure must never take the process down
            print(f"[V7S-DM-TVZ] FATAL, restarting thread loop in {_RESTART_DELAY_SECONDS:.0f}s: {exc!r}")
            time.sleep(_RESTART_DELAY_SECONDS)


def _run_nlb_nsb_watcher() -> None:
    while True:
        try:
            nlb_nsb_watcher.main()
        except Exception as exc:  # noqa: BLE001
            print(f"[V7S-DM-NLBNSB] FATAL, restarting thread loop in {_RESTART_DELAY_SECONDS:.0f}s: {exc!r}")
            time.sleep(_RESTART_DELAY_SECONDS)


def _run_ict_ob_watcher() -> None:
    while True:
        try:
            ict_ob_watcher.main()
        except Exception as exc:  # noqa: BLE001
            print(f"[V7S-DM-ICTBLOCK] FATAL, restarting thread loop in {_RESTART_DELAY_SECONDS:.0f}s: {exc!r}")
            time.sleep(_RESTART_DELAY_SECONDS)


def _run_major_minor_watcher() -> None:
    while True:
        try:
            major_minor_watcher.main()
        except Exception as exc:  # noqa: BLE001
            print(f"[V7S-DM-MAJORMINOR] FATAL, restarting thread loop in {_RESTART_DELAY_SECONDS:.0f}s: {exc!r}")
            time.sleep(_RESTART_DELAY_SECONDS)


def main() -> None:
    print("[V7S-DM] starting -- sub-components: tv_scraper (thread), nlb_nsb_watcher (thread), "
          "ict_ob_watcher (thread), major_minor_watcher (thread)")
    t1 = threading.Thread(target=_run_tv_scraper, name="v7s-dm-tvz", daemon=True)
    t2 = threading.Thread(target=_run_nlb_nsb_watcher, name="v7s-dm-nlbnsb", daemon=True)
    t3 = threading.Thread(target=_run_ict_ob_watcher, name="v7s-dm-ictblock", daemon=True)
    t4 = threading.Thread(target=_run_major_minor_watcher, name="v7s-dm-majorminor", daemon=True)
    t1.start()
    t2.start()
    t3.start()
    t4.start()
    # daemon=True so Ctrl+C / process exit doesn't hang on a stuck thread; join() here just
    # keeps the process itself alive as long as any thread is (all restart on their own
    # exceptions, so in practice this blocks forever under normal operation).
    t1.join()
    t2.join()
    t3.join()
    t4.join()


if __name__ == "__main__":
    main()
