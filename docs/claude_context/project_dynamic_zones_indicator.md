---
name: project-dynamic-zones-indicator
description: "Dynamic Zones indicator (Pine + MQL5 port) -- formula, status, pending XAUUSD mismatch check"
metadata:
  node_type: memory
  type: project
  originSessionId: 20623827-d3a1-4cf9-bd72-efa727ce8b32
  modified: 2026-09-26T20:14:26.144Z
---

Dynamic Zones = daily open +/- SMA(high-low)/2 of previous COMPLETED days (5 days -> Z1/Z3, 10 days -> Z2/Z4).
Pine fix (2026-09-26): old ChatGPT Pine used security(dayrange[1]) without lookahead -> ranges were one day stale; fixed with `[1]` + lookahead_on; user confirmed it then matched the reference red/green dotted lines exactly.
MQL5 port: `mql5/Dynamic Zones.mq5` (committed fe0ed40), copied + compiled into all 5 MT5 terminals' MQL5\Indicators, blue lines (clrBlue). Old MT5 version (used yesterday's open, iOpen shift 1) was overwritten + deleted at user's request.

2026-09-27: XAUUSD mismatch on MetaTrader5-5 (Exness GMT+0) confirmed -- cause: Exness D1 starts 00:00 + tiny separate Sunday bar; user's TV feed day starts at NY 17:00 close (=22:00 server) with Sunday folded into Monday. Fixed in v1.10: days built from H1 with InpDayStartHour (-1 auto = weekly-open hour: gold 22, BTC 0). Python reproduction with 22:00 start matched the screenshot (Fri 09-25: Z1 4310.24 Z2 4318.28 Z3 4237.17 Z4 4229.14). v1.10 copied to all 5 terminals, NOT yet committed; user still to confirm on chart.

**Why:** broker day boundaries differ from TradingView's; formula itself is correct.
**How to apply:** any future mismatch on another broker/terminal -> check its D1 day boundary / Sunday bars first.
