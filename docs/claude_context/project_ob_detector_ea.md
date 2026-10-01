---
name: project-ob-detector-ea
description: OB_Detector v1.07 indicator + V6S_ICT_3.0 EA (renamed from OB_EA v1.03 on 2026-10-02); sim results = no consistent edge; execution confirmed by user
metadata:
  node_type: memory
  type: project
  originSessionId: c57e7cda-2d34-480b-aaf5-f6aaa335e66b
  modified: 2026-09-30T21:39:43.523Z
---

Order-block work started 2026-09-30 (IST):
- `mql5/OB_Detector_v1.06.mq5` (+ .ex5, committed fafbcf9): volume-pivot OB port of a Pine script (user asked to remove the original author's name completely), closed-bar OBs, LIVE first-retest, 30 EA buffers (3 slots/side: top, btm, OB time, formed time, retest time), DrawObjects input. In all 5 terminals' Indicators.
- v1.07 (2026-10-01): fixed object prefix OBD_<len>_ -- v1.06's random prefix left stale zones saved in chart profiles; in all 5 terminals. v1.06 and OB_EA v1.00-1.02 deleted from all terminals + repo 2026-10-02 (7cec4a3); only v1.07 / EA v1.03 remain.
- EA now `v6s_ict_3/V6S_ICT_3.0.mq5` (MetaTrader5-5 Experts\V6S_ICT_3), renamed 2026-10-02 from OB_EA v1.03 (user: remove all OB_EA names/versions; separate from V6S-ICT v2.21 which is never to be touched); same logic, magic 26100101: most recent OB takes control, flip closes opposite trades, CISD (LuxAlgo Classic default, AlgoAlpha selectable) on a later candle -> entry, SL OB +/-0.5, TP 1:2, draws CISDs. User confirmed 2026-10-01 execution works ("just wanted to check execution") -- not a strategy approval.
- Sim `v6s_ict/sim/s_ob.py` + `build_ob_data.py` (m1_2y_tv.npy, repaired data). Every variant tested lacked month-on-month consistency; best = M15+H4 zones, V6S-ICT direction + DZ filter, immediate entry at touch when CISD already agrees, 1:1: +46.9k/2y, drop 8.3%, 7/24 losing months, 205 trades (picked in-sample).

**Why:** user evaluates by [[feedback-consistency-over-net]]; this OB idea hasn't met it.
**How to apply:** don't present OB_EA as a profitable strategy; next steps the user may pick: out-of-sample check (pre-Oct-2024 data), or build the M15+H4 V6S-direction variant.
