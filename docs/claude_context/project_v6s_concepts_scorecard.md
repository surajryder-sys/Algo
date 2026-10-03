---
name: project_v6s_concepts_scorecard
description: "V6S-ICT concept scorecard (gold, as of 3 Oct 2026) -- which ideas WORKED and which FAILED, with numbers; read before testing the same concepts on BTC or any new symbol"
metadata:
  node_type: memory
  type: project
  originSessionId: 55f6ad35-c61d-42ac-81be-09537ab7a106
  modified: 2026-10-03T17:30:58.765Z
---

Scorecard of every V6S-ICT concept tested on XAUUSD (clean Trial12 / Real7 data, Oct 2024 – Oct 2026, sim + MT5 tester).
Full detail and every number: `v6s_ict/HANDOVER.md` (§3–§5b, §6 BTC). The user is now applying the same concepts to BTCUSD
on another chart (3 Oct 2026). Use this to skip ideas that already failed and to re-test only those likely to behave
differently on BTC. Related: [[project_v6s_ict_ea]] (gold EA), [[project_v6s_ict_btc]] (old BTC notes, pre-clean data),
[[feedback_consistency_over_net]].

**Why:** the user asked (3 Oct) to keep a durable list of what worked and what didn't, so the BTC work doesn't repeat failed tests.
**How to apply:** when a BTC/new-symbol idea matches a row below, quote the gold result first. Re-test only where BTC
differs: 24/7 sessions, weekends, much wider ranges, no quiet-market problem. Never treat a gold "worked" as proven on BTC.

## WORKED on gold (kept in the EA)
- **Aligning levels:** same price on ≥3 of H4/H2/H1/M30/M15/M10/M5/M3 Major/Minor (pivot 5), ≥2 Major. Touch buffer 0.
- **Entry = AlgoAlpha CISD (tol 0.7) on M5** after the touch (reversal) or after the close through the level (breakout, valid 48 bars).
- **Reversal SL** = extreme of the whole touch run ±0.5, cap 20 (v2.22; +42k cent vs SL after the last touch).
- **Breakout SL** = broken level ∓1.5 (v2.18; cut square-offs 144 → 37). Breakouts need TP ≥ 1.5R.
- **Targets:** reversal TP = next daily pivot ∓1.0, then session high/low (v2.19). Breakout TP = next aligning level ∓1.0. 1:1 if under 1R.
- **Management:** breakeven at 2R (1R and 1.5R tested worse), partial at 7R (rarely hit), auto square-off on an opposite M5 CISD back through the level.
- **Extra leg** when the main trade is in profit (TP 1:3, main to BE). Roughly neutral.
- **Filters:**
  - Night reversal block 01:30–05:30 IST, Monday reversal block, London aligning block 12:30–18:30 IST.
  - DZ filter: one-way for the session; turning it off was −59k.
  - **Quiet-market filter (v2.23):** no entries while the 10-day average daily range is < 30. Fixed the dead 2024 / early-2025 months: 8 → 6 losing months in the tester, 5 → 3 on Real7.
- **DZ slot:**
  - Gap-session zones. Entry on **M15 LuxAlgo Classic CISD** closing beyond the zone, square-off on AlgoAlpha (+95k vs AlgoAlpha entry).
  - SL = far zone line ±6, cap 75; capped trades need ≥1R. TP = nearest aligning level ∓2.0.
  - DZ is the biggest earner, about 60% of profit.
- **Data lessons:** the .hcc parser bug (fake 00:00 candles) inflated every sim before 30 Sep. Real ticks cost about 12% vs ticks rebuilt from M1 bars. Always confirm in the tester.

## FAILED on gold (rejected; don't re-test without a reason)
- **Trap trade:**
  - Reverse on the auto square-off. Off since v2.17: −$548 in the tester.
  - Rechecked on v2.23 / Real7: 67 trades, −$135.
- **IFVG instead of CISD:**
  - Entry on M5/M3/M1, strict or loose: −$4.5k to −$15k. Best was strict M1 at +$47.0k vs +$51.6k.
  - DZ on M15/M10: −$22k to −$30k. Loose M3 gave 3 losing months but more drop and −$8.8k.
  - IFVG exit: a wash.
  - Why it fails: it fires late and rarely, and misses clean breakouts.
- **Dual ATR Trail flip** instead of CISD (entry or exit): rejected.
- **Earlier breakeven:**
  - Aligning at 1R / 1.5R: −$2.2k / −$0.8k.
  - DZ breakeven at 1R / 1.5R / 2R: −$3.5k to −$6k. DZ trades retrace to entry before running.
  - But see BTC below, where DZ breakeven did help.
- **DZ trigger timeframe:** M10 −$12.3k, M5 −$11.2k, M30 worse on gold. M15 is best (on BTC M30 is best).
- **DZ reversals** (fade the zone): lose alone; with breakouts, twice-as-deep losing months.
- **Exits:**
  - New-level + opposite CISD exit, basket close at $ targets, exit at the next opposite level.
  - Lifting the DZ filter on a close back inside.
  - Breakout replacing an open reversal.
- **New-level entries:** a just-confirmed level is known too late (about 50 min on M10).
- **Other settings, all kept as they are:**
  - SL cap 30/15.
  - Touch buffer 1.0.
  - CISD tolerance 0.5/0.9.
  - Breakout validity 24/96.
  - DZ SL buffer 2.
  - DZ cap 50/100.
  - DZ fixed 1:1–1:2 TP.
  - DZ London block (on gold).
  - Blocking opposite slots.

## BTC so far (other chat, `HANDOVER.md` §6, Real7 clean data, settings = gold × 35)
- **Works on BTC:**
  - DZ breakouts on **M30 AlgoAlpha** (LuxAlgo and M15 worse).
  - Aligning reversals only with stop ≥ 300.
  - DZ fixes: max 1 DZ trade per IST day, no DZ square-off, London block, **DZ breakeven** (31% of 1:2 losers were 1R up first).
  - DZ entry windows 05:30–12:30 + 18:30–21:00 IST.
  - No "Saturday" (really Friday US night); Sunday DZ trades win.
- **Fails on BTC:**
  - Aligning breakouts (−32k to −51k) and extra legs.
  - Trend/time filters stacked on breakeven, weak-breakout filter, breakeven on a 1:1 TP.
  - DZ entries 00:00–05:30 IST (they caused the 2023 / H1-2024 losses).
- **Best BTC set (tester v1.02):** windows + no Saturday + TP nearest level + BE 1R + reversals. Sim +121k USC over 44 months, drop 17.2%, 10/44 losing months, every year up. Tester confirmation pending.
- **Concept differences gold vs BTC:**
  - Breakouts: aligning breakouts are gold's second earner but lose on BTC.
  - DZ breakeven hurts gold but helps BTC.
  - DZ timeframe: M15 LuxAlgo on gold, M30 AlgoAlpha on BTC.
  - London block: aligning only on gold; DZ too on BTC.
