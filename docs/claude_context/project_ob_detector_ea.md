---
name: project-ob-detector-ea
description: OB_Detector v1.07 indicator + V6S_ICT_3.0 EA (renamed from OB_EA v1.03 on 2026-10-02); sim results = no consistent edge; execution confirmed by user
metadata:
  node_type: memory
  type: project
  originSessionId: c57e7cda-2d34-480b-aaf5-f6aaa335e66b
  modified: 2026-10-03T17:24:08.523Z
---

Order-block work started 2026-09-30 (IST):
- `mql5/OB_Detector_v1.06.mq5` (+ .ex5, committed fafbcf9): volume-pivot OB port of a Pine script (user asked to remove the original author's name completely), closed-bar OBs, LIVE first-retest, 30 EA buffers (3 slots/side: top, btm, OB time, formed time, retest time), DrawObjects input. In all 5 terminals' Indicators.
- v1.07 (2026-10-01): fixed object prefix OBD_<len>_ -- v1.06's random prefix left stale zones saved in chart profiles; in all 5 terminals. v1.06 and OB_EA v1.00-1.02 deleted from all terminals + repo 2026-10-02 (7cec4a3); only v1.07 / EA v1.03 remain.
- EA now `v6s_ict_3/V6S_ICT_3.0.mq5` (MetaTrader5-5 Experts\V6S_ICT_3), renamed 2026-10-02 from OB_EA v1.03 (user: remove all OB_EA names/versions; separate from V6S-ICT v2.21 which is never to be touched); same logic, magic 26100101: most recent OB takes control, flip closes opposite trades, CISD (LuxAlgo Classic default, AlgoAlpha selectable) on a later candle -> entry, SL OB +/-0.5, TP 1:2, draws CISDs. User confirmed 2026-10-01 execution works ("just wanted to check execution") -- not a strategy approval.
- Sim `v6s_ict/sim/s_ob.py` + `build_ob_data.py` (m1_2y_tv.npy, repaired data). Every variant tested lacked month-on-month consistency; best = M15+H4 zones, V6S-ICT direction + DZ filter, immediate entry at touch when CISD already agrees, 1:1: +46.9k/2y, drop 8.3%, 7/24 losing months, 205 trades (picked in-sample).

2026-10-03 (Real7 ticks, all uncommitted, user paused here "let's rest here"):
- V6S_ICT_3.15 (fresh M5 Major -> MSS -> M3/M5 OB pullback, 1:1, magic 26100501) built; user's Trial12 tester run: 351 trades +$868; 308/336 trades matched sim (diffs = feed differences, one -$153 gap through the daily break).
- algo_v2 Python bot replayed in `v6s_ict/sim/s_algov2.py`: as built -338 pts/8.7k trades. Best filters: H4+M15 CISD agree, DZ breakout + DZ deep, risk 3-20, no 01-04 IST, max 3/day, skip M3 pending, M1-M30 sources -> +1,615 pts @0.01, drop 236, 5/24 losing months.
- OB retest study `s_obr.py` / `an_obr.py` / `pf_obr.py` (first retest -> M1/M3/M5 CISD): only H2/H1 OBs have an edge (M15/M10/M5 none; 1:1 never works; BE/partials don't help; trend filters HURT here). Best: H2+H1, M5 LuxAlgo CISD, SL OB edge cap 20, TP 2R, OB height < 15 pts, no Friday, one position -> +$5,551 @0.04, drop $578, 5/23 losing months, both years positive.
- Best OB version later: height < 20 AND < 0.25 x 10-day ADR, 2R, no Friday, 1 pos, first CISD only: 306 trades +$13,810 @0.1, DD $1,132, 2/23 losing; extra positions / re-entries / exits / brakes / fixed-$ risk all worse (pf2_obr.py).
- `v6s_ict_3/test5.0.mq5` (3 Oct): v2.23 copied unchanged + OB retest slot (magic 26100601, OBLots 0.10); compiled in "MetaTrader 5" (Real7) and MetaTrader5-5 Experts\V6S_ICT_3. User to run one real-data tester test and send the report.
- test5.0 Real7 tester (Jan 2024 - Sep 2026): +$66,717 (v2.23 +53,490 / OB +13,227), 4 losing months of 33; OB weak in unseen Jan-Oct 2024 (+573, sim +581 matched). Real7 data extended to Jan 2024 (`build_real7.py extend` -> ticks_real7x.npz / m1_real7x_tv.npy; sims take Data=real7x).
- Fix found: OB stop < 5 pts loses in every period -> `v6s_ict_3/test5.1.mq5` = test5.0 + OBMinStop 5 (OB comments "T51 OB"); sim OB +$14,999, combined est. +$68,863 with 1 losing month (Aug 2024). Quiet-market (ADR) switch did NOT help. Compiled in both terminals; user to tester-run.
- test5.1 Real7 tester: +$67,554, OB +$14,061, 3 losing months (Mar 24 -11, Aug 24, May 25); saved in v6s_ict_3/test5_monthly.csv (test5_compare.py add/split).
- Series built 3 Oct (user-chosen names; std + cent x3 + half /2 copies, all compiled in "MetaTrader 5" + MT5-5): `v6s_ict/V6S_ICT_v2.25` (= v2.23 + new comments V6S-ICT-ALG-<TFs>, ALG-BO|BD-<TFs>, TRP, EXLEG, DYZ-BO|BD), `v6s_ict_3/V6S_ICT_OB_3.25` (OB slot alone, magic 26100601, V6S-ICT-OB-H1|H2), `v6s_ict_3/V6S_ICT_OB_v5.00` (combination series). Old files NOT deleted (user: "nothing yet").
- 3 Oct late: code review fix applied IN PLACE to 3.25 / v5.00 (all copies + 1LOT): OB slot restart safety (OTDecided: an already-retested OB whose first CISD came before a reload is not re-armed) + IndicatorRelease on deinit. The other chat merged terminal EA folders: all V6S EAs now live in `Experts\V6S_ICT` (no more `Experts\V6S_ICT_3`): v5.00 copies (std/cent/half/1LOT) in the main folder, older lines in subfolders (renamed 3 Oct, commit 3f5526b) `V6S_ICT_v2.25\`, `V6S_ICT_OB_3.25\`, BTC tester subfolder — check the live layout before installing, the other chat reorganises it. Install new builds into that layout. Fix committed 3 Oct (after 97ca896).
- Pending ideas offered: OB height relative to ATR (fixed 15 pts starves high-volatility periods, e.g. Mar 2026 = 0 trades), then build as V6S_ICT_3.16 EA for the tester.

**Why:** user evaluates by [[feedback-consistency-over-net]]; this OB idea hasn't met it.
**How to apply:** don't present OB_EA as a profitable strategy; when the user resumes OB work, start from the H2/H1 retest result above.
