# V3 Sentinel — Periodic Health Check Log

Recurring check (every ~30 min) of: process health, log errors, current
bias/active trades, and real MT5 account state. Each entry below is one
check. Newest entries at the top.

Not committed to git (state/log file, see .gitignore) -- purely a
running record for the user to review.

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
