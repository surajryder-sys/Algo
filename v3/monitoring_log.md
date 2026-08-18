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
(1920.11) -- no log line anywhere shows 1921.75 being set by our code,
so this is likely broker-side fill/execution mechanics (slippage +
minimum-stop-distance auto-adjustment), not a bug. Results in a WIDER
SL than intended (more safety margin, not less), and will self-correct
the moment Stoploss Manager's own trailing logic first computes a real
move for this position (it always computes the desired SL fresh from
entry_price + peak_favor, never from "current SL + delta"). Worth
keeping an eye on whether this recurs on future fills; not acted on
yet.

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
