# V3 Sentinel — Periodic Health Check Log

Recurring check (every ~30 min) of: process health, log errors, current
bias/active trades, and real MT5 account state. Each entry below is one
check. Newest entries at the top.

Deliberately trackable in git (not gitignored like the raw `*.log`
files) -- this is a curated summary worth keeping history on, not raw
log noise.

---

## 2026-08-19 (major incident + fixes -- duplicate orders, BTCUSD data outage)

**Execution Bridge race condition -- CONFIRMED, FIXED, DEPLOYED.**
`_reconcile` acted on the same `desired_state` snapshot `_check_disappeared`
had just proven stale within the same cycle, causing two distinct real
incidents: (1) a filled XAUUSD pending order got duplicated up to 4x in
~11 seconds (128050095/100/111/117), 3 left permanently untracked --
confirmed a SECOND, smaller-scale prior occurrence the same day (06:30,
self-corrected, previously unnoticed); (2) the user's own manual close
of a live position was silently undone/reopened within 1-4 seconds,
twice, before the fix was deployed. Fixed in `execution_bridge.py`:
`_check_disappeared` now returns whether it found+cleared a
disappearance, and `run_once` skips `_reconcile` for that symbol for
the rest of that cycle when it did -- the source Manager gets one full
cycle to react before Execution Bridge trusts its state again. User
manually closed all 3 orphaned XAUUSD duplicates + confirmed the
account is flat. Deployed live (new process, verified via fresh PID).

**BTCUSD tv_scraper was dead for ~3 days -- CONFIRMED, FIXED.** Process
count still looked normal (3 alive) but was actually 2 duplicate XAUUSD
instances (racing for the same browser tabs since Aug 17) + 1 ETHUSD --
no BTCUSD-configured process existed. `tv_scraper_run.log` (BTCUSD's
own log) last wrote Aug 16 21:35, using an outdated 1x2 grid layout
that predates the current 6x1 config -- meaning it had already fallen
behind before dying. Every BTCUSD Trend/Reversal Manager decision since
then ran on frozen zone data; no clean way to retroactively separate
which trades that actually affected. Restarted BTCUSD (attached to the
already-open browser window, no data loss to the window itself, just
nothing had been reading/writing it), removed the duplicate XAUUSD
instance, restarted ETHUSD too for the code fix below. All 3 confirmed
independently reading correct, distinct real prices post-restart.
**User asked to watch BTCUSD's next trades more closely than usual
given this.**

**Zone-history logging added.** New persistent, append-only
`tv_scraper_<symbol>_zone_history.jsonl` per symbol -- ZoneStore itself
deletes a zone on mitigation, so a trade's origin zone was often
already gone by the time a "where did that OB come from" question came
up. Now every newly-formed zone (range, timeframe, direction, times) is
durably logged the first time it's seen, independent of the live
top-4-style store's own churn.

**Also investigated, not a bug:** a XAUUSD sell fired off a real,
formed_time_confirmed M5 bearish parent OB (08:25 IST) the user
couldn't spot on the live chart -- data-side looked legitimate
(non-fallback timestamp, sane range), but couldn't be independently
re-verified since the zone had already aged out of the live store by
the time it was checked; the new zone-history log above closes that gap
for next time.

---

## 2026-08-19 (first check, set up on user request)

**Processes**: all 8 confirmed running -- 3x tv_scraper (XAUUSD/BTCUSD/ETHUSD),
alert_manager.watcher, alert_manager.telegram_commands, trend_manager,
reversal_manager, execution_bridge.

**Errors found, investigated:**
- Alert Manager: historical MT5 IPC failures (auto-reconnect already
  handled these, confirmed self-recovered, not ongoing).
- Execution Bridge: `retcode=10016 Invalid stops` on an SL move for an
  earlier BTCUSD position (broker rejected a trail attempt that got
  too close to market price at that instant) -- that position has
  since closed (via legitimate OB mitigation, confirmed in
  reversal_manager's own log: "active trade's entry OB was mitigated
  -- treating as closed"). Not a bug, normal broker minimum-stop-
  distance behavior; Stoploss Manager just retries next cycle.

**New observation, not urgent**: currently-open ETHUSD position's real
SL (1921.75) doesn't match what Reversal Manager decided at entry
(1920.11) -- no log line anywhere shows 1921.75 being set by our code.
**Update, same session**: user confirmed this was their own manual
change, not broker mechanics. Revealed a real gap -- Stoploss Manager
had never touched this position's SL itself, so its manual-override
detection had no baseline to compare against, meaning the change was
NOT actually protected and would have been silently overwritten the
moment trailing first crossed the breakeven threshold. Fixed
(commit 2bc54e0): SymbolSLState now seeds last_managed_sl from
whatever the real SL already is the first time a position is ever
examined. Restarted and verified: baseline correctly picked up the
user's own 1921.75 as the new protected value going forward.

**Bias**: XAUUSD bearish/bearish, BTCUSD bearish/bearish, ETHUSD
bullish/bearish (Structure/Short-term).

**Active trades**: Trend Manager -- none. Reversal Manager -- ETHUSD
short (entry 1913.07 decided / 1914.8 real fill, SL 1920.11 decided /
1921.75 real, entry_start_time formed 2026-08-18 22:45 IST, retested
2026-08-19 01:00 IST).

**Real MT5 account**: 1 open position (ETHUSD, ticket 127984178).
Balance $2,044.96, equity $2,048.93 (floating +$3.97 on the open
ETHUSD short). Demo account, no real money.

**Overall**: healthy. No active bugs, no unresolved errors. One
observation flagged for continued watching (SL discrepancy above).

---

## 2026-08-19 (recurring check)

**Processes**: all 8 confirmed running.

**Errors found**: same two historical entries as last check (BTCUSD
`Invalid stops` from an already-closed position, Alert Manager's
already-recovered IPC errors) -- both confirmed still historical, not
recurring (both logs' most recent lines show clean, successful
activity, no growth in the error lines since last check).

**Bias**: XAUUSD bearish/bearish, BTCUSD bearish/bearish, ETHUSD
bullish/bearish.

**Active trades**: Trend Manager -- none. Reversal Manager -- ETHUSD
short unchanged from last check (SL still correctly holding at the
user's own manually-set 1921.75, now protected by the baseline fix).

**Real MT5 account**: 1 open position (ETHUSD). Balance $2,044.96,
equity $2,046.75 (floating +$1.79). Demo account.

**Overall**: healthy, stable, no new issues since last check.
