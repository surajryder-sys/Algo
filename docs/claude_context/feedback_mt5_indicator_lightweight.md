---
name: feedback-mt5-indicator-lightweight
description: User wants all MT5-side indicators/EAs kept as lightweight as possible -- heavy per-tick/per-timer work causes real chart lag (candles literally stop forming)
metadata: 
  node_type: memory
  type: feedback
  originSessionId: b1bfb561-670a-4277-b383-7a5fdd4527e2
  modified: 2026-08-27T18:33:47.011Z
---

Standing design constraint for any MT5-native (.mq5) work in this repo,
not specific to one indicator: the user has experienced real chart lag
(candles literally stop forming) when an attached indicator/EA does too
much work per tick or per timer callback.

**Why:** MT5 charts run indicator calculations on the UI/chart thread. An
indicator that rescans full history, does heavy per-object loops, or
polls too frequently on every tick/timer directly blocks candle
rendering/formation on that same chart -- this is a real, previously
observed failure mode, not a theoretical concern.

**How to apply:**
- Prefer computing only what changed (e.g. last closed bar) over
  rescanning full history every call -- see
  `mql5/OB_ATR_Bridge_Indicator_v1.00.mq5`'s closed-bar-only publish
  pattern, reused in `mql5/SurajBot_ATRTrail_FINAL_LIVEFIXED_REALTIME_DUAL.mq5`.
- Avoid on-chart UI (Comment() panels, labels, buttons) unless explicitly
  requested -- pure visual sugar costs real per-tick work for nothing V4
  needs. `mql5/OB_Zone_Bridge_Lite.mq5` and the ATR dual indicator were
  both stripped of all panel/label/button code from their
  `OB_StatePublisher_Indicator_v2.00.mq5` ancestor for exactly this reason.
- Watch timer-driven full rescans specifically: `OB_Zone_Bridge_Lite.mq5`'s
  `ScanEverySeconds` timer re-walks every chart object AND reconstructs
  historical retest status (iBarShift/iClose/iHigh/iLow loops) for every
  zone on every single scan -- a real candidate for the lag being
  described, not yet optimized to skip already-resolved
  (non-virgin/already-live-visited) zones on repeat scans. Flagged to the
  user 2026-08-28, not yet acted on -- revisit if lag reappears with V4's
  indicators attached.
- When in doubt, offload computation to the Python side (reading the
  published bridge file) rather than adding MT5-side logic -- matches the
  user's own explicit "offload things which are no longer required"
  framing. See [[project_v4_xauusd_architecture]] for the overall
  MT5-bridge-vs-Python split this applies within.
