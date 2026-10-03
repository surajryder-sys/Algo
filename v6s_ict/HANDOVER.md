# V6S-ICT — full handover (state as of 1 Oct 2026, IST)

> **READ FIRST (1 Oct 2026): every 2-year simulation result before 30 Sep 2026 was inflated by bad data.** The M1
> files were parsed from MT5 `.hcc` history; on ~85 % of days the 00:00 UTC record was a wrong whole-day candle, which
> produced fake fills. Clean, tester-matched numbers are in §4; §5's old table is kept for history only (see §5a for
> the re-check on clean data). The Strategy Tester is the reference now.

Written for a teammate / a new Claude chat picking this up with no prior context. Read this first, then
`v6s_ict/sim/README.md` (how to re-run every test) and `docs/claude_context/` (the previous assistant's saved notes
about the whole repo and the user's working preferences).

---

## 0. Working rules the user (Suraj) expects

- **Report every time in IST (UTC+5:30).** Exness server time = UTC. Gold's daily session starts 03:30 IST (22:00 UTC).
- **Every EA change = a NEW versioned file** `<EA>_v<x.yy>.mq5`; never overwrite an old one (one exception the user
  explicitly asked for: a display-only panel fix was overwritten in place in v2.09).
- **Copies of each version:** `V6S_ICT_EA_vX` (base lots), `V6S_ICT_EA_cent_vX` (3×: 0.24/0.30/0.30), and from v2.22 an
  optional `V6S_ICT_EA_half_vX` (0.04 reversals / 0.05 breakouts + DZ / runner 0.01, user 2 Oct),
  `V6S_ICT_EA_cent_1lot_vX` (1.00 everywhere — **this one runs live**).
- After building: copy + compile in **both** terminals (below), delete older versions from the terminals **except
  the one attached to the live chart** (delete it after the user swaps), commit + push.
- **Never attach or run an EA live, and never do GUI actions** — the user attaches/swaps EAs himself. Read-only
  MetaTrader5-python queries (account, deals, candles) are fine.
- **Test first, change only when asked.** "Just checking / no changes" means simulate only.
- Delete throwaway test/scratch files when done. One question at a time when something is ambiguous.
- Don't suggest the Vantage grid EA as a design basis (`research/` is analysis-only).

## 1. Accounts, terminals, symbols

| Terminal | Path / data folder | Account | Use |
|---|---|---|---|
| **MetaTrader 5** | `C:\Program Files\MetaTrader 5` · data `...\Terminal\D0E8209F77C8CF37AD8BF550E51FF075` | **Exness REAL cent 263602422** (Exness-MT5Real37, USC). Balance 77,581 USC when the EA went live (28 Sep 04:14 IST) | **LIVE**: `V6S_ICT_EA_cent_1lot_v2.22` on **XAUUSDc M5** (attached 2 Oct 17:16 IST; demo MT5-5 gets `V6S_ICT_EA_v2.22`). A power cut ~15:10 IST 1 Oct dropped the EA from both charts — **after any restart check the EA reloaded**. |
| **MetaTrader5-5** | `C:\Program Files\MetaTrader5-5` · data `...\Terminal\4DB333B24A74B726D7AA441A9D0137DC` | **Exness Trial12 demo 83125455** (Exness-MT5Trial12, XAUUSD) | **Strategy Tester** (the user runs it; real ticks from 2026-01-01, generated before) + compile target; the sim data `m1_t12.npy`/`ticks_t12.npy` come from here |

EA folder in both: `MQL5\Experts\V6S_ICT\`. Compile from the command line:
`& "<terminal dir>\MetaEditor64.exe" /compile:"<data>\MQL5\Experts\V6S_ICT\<file>.mq5" /inc:"<data>\MQL5" /log:"<log>"`.

Contract specs (cent account): **XAUUSDc 1.00 lot = 100 USC per $1**, spread ≈ 0.24–0.26, no commission, long swap
≈ −56 USC/lot/night. BTCUSDc 1.00 lot = 1 USC per $1 (spread $10). ETHUSDc 1.00 lot = 1 USC per $1 (spread $1).
USOILc 1.00 lot = 1,000 USC per $1 (spread 0.02, short swap −186 USC/lot/night).

## 2. What the EA does — v2.22 (current, live since 2 Oct 17:16 IST)

File: `v6s_ict/V6S_ICT_EA_cent_1lot_v2.22.mq5` (identical logic in the other two copies). Chart: XAUUSDc, any TF
(M5 recommended). All inputs at their defaults are the tested/agreed settings.

**Levels.** Major/Minor support & resistance (ZigZag, pivot period 5, ported verbatim from the indicator's
"yellow line" logic) on 8 TFs: H4 H2 H1 M30 M15 M10 M5 M3. **Aligning level** = the exact same price on ≥3 TFs,
with ≥2 of them Major.

**Slot 1 — aligning-level trades** (magic 26092701, 1.00 lot, one main trade at a time):
- *Reversal:* an M5 candle touches an aligning level (touch never expires); a LATER M5 CISD (AlgoAlpha port,
  tolerance 0.7) closes back beyond it → trade. **SL = extreme of the WHOLE touch run (first touch candle → CISD) ± 0.5 (v2.22,
  `SLFromTouch`; was: after the last touch candle)**, **capped at 20**.
  **No reversal entries 01:30–05:30 IST** (v2.11) **or on Mondays IST** (v2.21, `UseMondayRevBlock`).
- **London block (v2.21, `UseLondonBlock`):** no new aligning-slot entries (reversals, breakouts, legs) 12:30–18:30 IST;
  a blocked setup is not used up. DZ slot unaffected.
- *Breakout:* M5 close through a level (valid 48 candles, cancelled by a close back through), then a LATER M5 CISD
  beyond it → trade. **SL = the broken level ∓ 1.5 (`BreakoutSLAtLevel`, `BreakoutSLBuffer`, v2.18)** — was the CISD
  swing ± 0.5 — capped at 20. Only if the TP is ≥ 1.5R.
- *TP:* **reversals → next classic daily pivot (v2.19, `UsePivotTPRev`)**: PP/R1-R3/S1-S3 from the previous gap-session's
  H/L/C, ∓1.0; if no pivot is ahead → the session target (v2.14): nearest beyond price of the previous gap-session's high
  (buy) / low (sell) and the current session's high/low so far (closed M5), ∓1.0; fallback = nearest aligning level
  ∓1.0. **Breakouts → nearest opposite aligning level ∓1.0.** Either way: if the target is < 1R from entry → 1:1
  (v2.06). Re-checked every M5 candle.
- *Management:* breakeven at 2R; partial close at 7R keeping 0.10 runner; **auto square-off** when an opposite M5
  CISD closes back beyond the trade's level; an opposite setup closes & reverses.
- *Trap trade (v2.15, `UseTrapTrade`, **OFF by default since v2.17** — it lost on clean data):* when the auto square-off closes a reversal/breakout (the setup failed),
  enter the OTHER way at once: SL = failed level ± 4.0 (`TrapSLBuffer`, capped 20), TP fixed 1:2 (`TrapTPR`),
  LotSize, same magic. Only while the original trade is open (not after an SL/BE exit); a trap trade is never
  flipped again; not blocked by the DZ filter / night block. Comment `V6SICT TB|TS <level>`.
- *Extra leg (v2.12, magic 26092731):* first trade **in profit** + new same-side setup → one extra leg (same lot,
  setup SL capped 20, **TP 1:3 since v2.19** (`ExtraLegTPR`, was 1:2)) and the first trade's SL → breakeven. Max one leg; it closes with the first trade on
  an opposite setup / square-off.
- *DZ filter:* an M5 close beyond the outer zone line blocks the opposite direction for the rest of the session.
- Off by default: brake, M10-after-SL, news filter. Spread filter 0.50; no entries in the first 60 min of the week.

**Slot 2 — Dynamic Zones (DZ) breakouts** (magic 26092721, 1.00 lot, independent of slot 1):
- Zones per **gap-delimited session** (v2.13, same as `mql5/Dynamic_Zones_CISD_MajorMinor.mq5`): a new session
  starts wherever consecutive H1 bars are > 1 h apart; anchor = session's first H1 open; Z1/Z3 = open ± ½ avg range
  of the previous 5 complete sessions, Z2/Z4 = same with 10. Resistance zone = Z1–Z2, support zone = Z3–Z4.
- Entry: an **M15 CISD** candle closing beyond the zone (above resistance → buy, below support → sell). **Since
  v2.16 (`DZEntryLuxAlgo`) the entry CISD is LuxAlgo "Classic"** (close beyond the OPEN of the first candle of the
  latest opposite run; no tolerance; line expires after `LuxMaxBars` = 100 candles). The **square-off still uses
  AlgoAlpha**: only an AlgoAlpha opposite setup closes the DZ trade; a LuxAlgo opposite setup while a DZ trade is
  open is ignored.
- SL = far zone line ± **6.0 (v2.21, was 4.0)**, **capped at 75**; a capped trade needs TP ≥ 1R (v2.10). TP = nearest aligning level
  **∓2.0 (`DZTPBuffer`, v2.20; was 1.0)** fixed at entry (1:1 if none; skipped if < 1 pt away). An opposite DZ setup squares it off.

**Safety (v2.07–v2.09):** per-trade data flushed to disk and rebuilt from comment/history after a crash; startup
replays the current session's M5 candles with no orders; only a candle closed ≤ 120 s ago can trade; full state
reset on timeframe/symbol change (crash fix). Panel: font 10, rows wrap at the chart width and at MT5's 63-char
label limit.

## 3. Version history (all files kept in `v6s_ict/`)

| Ver | Change |
|---|---|
| v1.50–1.55 | Aligning-level reversals only (M5 CISD, BE 1:2, partial 1:7). v1.55 = real touch + SL after touch candle. |
| v2.00 | + opposite-level TP, breakouts (0.05 lot), auto square-off |
| v2.01 | + DZ filter |
| v2.02 | + 20-pt SL cap |
| v2.03 | "Option D": breakouts need TP ≥ 1.5R, lots 0.10/0.08, M10-after-SL & brake OFF |
| v2.04 | lots swapped: breakouts 0.10, reversals 0.08 |
| v2.05 | + DZ breakout slot (M15 CISD, zone SL +4, skip if > 75) |
| v2.06 | 1:1 TP when the level is < 1R; DZ skip when TP < 1 pt |
| v2.07 | restart safety (flush, replay, 120 s rule) |
| v2.08 | crash fix on timeframe change (ResetState) |
| v2.09 | DZ SL capped at 75 instead of skipped (+ panel fix, overwritten in place) |
| v2.10 | capped DZ trades need TP ≥ 1R |
| v2.11 | no reversal entries 01:30–05:30 IST |
| v2.12 | extra leg (in profit + first trade to BE) |
| v2.13 | Dynamic Zones on gap-delimited sessions (was plain D1 = 05:30 IST, wrong) |
| v2.14 | session-high/low TP for reversals |
| v2.15 | trap trade on the auto square-off (T6: SL level ± 4, TP 1:2) |
| tester v2.16 | `V6S_ICT_EA_tester_v2.16` (standard lots only): DZ entry on LuxAlgo CISD, exit AlgoAlpha |
| v2.17 | = v2.16 DZ LuxAlgo entry + trap trade OFF by default (3 copies) |
| tester v2.18 | breakout SL at the broken level ± buffer (input), standard lots |
| v2.18 | = v2.17 + breakout SL at the broken level ∓ 1.5 (3 copies) |
| tester v2.19 | reversal TP at the next daily pivot + extra-leg TP 1:3 (inputs) |
| **v2.19** | **= v2.18 + reversal TP at the next daily pivot + extra-leg TP 1:3** (3 copies) |
| tester v2.20 | v2.19 + separate `DZTPBuffer` = 2.0 for the DZ slot's TP (was the shared 1.0) |
| v2.20 | = v2.19 + DZ TP buffer 2.0 (3 copies). Tester: +$43,791, PF 1.41, 10 of 33 months losing, low $2,586 |
| tester v2.21 | v2.20 + DZ SL buffer 6, Monday reversal block, London aligning block (inputs) |
| v2.21 | = v2.20 + DZ SL buffer 6 + Monday reversal block + London aligning block (3 copies).
| **v2.22** | **= v2.21 + reversal SL from the whole touch run (`SLFromTouch` true)** (3 copies). Tester: **+$50,840, PF 1.55, deepest drop 32.7 %, low $3,460, 8 of 33 months losing (−$6,141); 2024 +$3,894 / 2025 +$15,819 / 2026 +$31,127** | Tester: **+$48,325, PF 1.54, deepest drop 35.4 %, low $3,344, 8 of 33 months losing, 2024 +$3,523** |
| **v2.23** | = v2.22 + quiet-market filter `MinADR` 30 / `ADRDays` 10: no new entries (aligning + DZ) while the average D1 range of the last 10 closed days (Sunday stubs skipped) is below 30 pts. Sim (Trial12, $5k, Jan 2024 – Sep 2026): v2.22 +$52,358, low $2,785, 8 losing months (−$6,612) → **+$54,090, low $4,437, 6 losing (−$4,120), 3 months no trades (Jan–Feb 2024)**; MinADR 28 +$54,323 / 6 losing, 32 +$52,437 / 7, 36 +$50,397 / 6. Profitable months 25 → 24 (losing months become flat ones). DZ 15:00/17:00 IST block cut drop $3,319 → $2,313 but not losing months (not added). **Strategy Tester (Trial12, $5k, Jan 2024 – Sep 2026): +$52,279, PF 1.64, deepest drop 20.8 % (v2.22 32.7 %), lowest balance $4,852 (v2.22 $3,460), 1,300 trades, 5 of 33 months losing (−$4,221: Jun 24 −69, Aug 24, May 25, Aug 25, Jul 26) + 4 with no trades (Jan–Mar 2024, Jan 2025); 2024 +4,814 / 2025 +16,338 / 2026 +31,127; Feb 2025 onward identical to v2.22.** 4 copies (standard, cent, cent_1lot, half); v2.22 removed from both terminals 3 Oct 2026 |
| v3.00/3.01 | hedge-basket experiments (not used) |

## 4. Headline results — CLEAN data (1 Oct 2026)

**Strategy Tester, Exness Trial12 XAUUSD, 1 Jan 2024 – 29 Sep 2026, $5,000, 0.08 rev / 0.10 BO + DZ, trap ON**
(27 % real ticks: 2026 real, 2024-25 generated from M1):

| | v2.15 | v2.16 |
|---|---|---|
| Net | +$27,885 | **+$37,523** |
| 2024 / 2025 / 2026 | −2,401 / +10,370 / +19,804 | −1,260 / +12,341 / +26,078 |
| Lowest balance | **$1,613 (21 Jan 2025), −73 %** | $2,323 (11 Nov 2024), −60 % |
| Losing months | 13 | 11 |
| DZ slot | −1,050 / +7,414 / +12,602 | +90 / +9,386 / +18,876 |

Aligning slot identical in both: reversals +$1,409 over 635 trades; breakouts TP +35.9k, SL −18.8k, **auto square-off
−11.4k (144 trades)**; trap trades −$548 over 194. Costs: swap −$2.5k, commission −$0.7k, spread ≈ −$2k.

**Main weakness = quiet markets.** Gold's typical daily range: 2024 26.6 pts, 2025 45.0, 2026 90.1. The EA's
fixed-point stops/targets/buffers suit 2026; in 2024 wins were ~3.4× smaller while costs were not → 2024 lost and
the balance fell 60-73 %. Best hours 18:00-23:00 IST (+$27k); worst 15:00-17:00 IST (−$2.7k) and 01:00-04:00 (−$1.7k);
2025 sells lost against the uptrend (−$1.4k vs buys +$13.8k).

**Strategy Tester v2.18** (same setup, trap off, breakout SL level ± 1.5): **+$39,050, PF 1.37, low $2,676 (−51 %)**,
2024 −509 / 2025 +14,023 / 2026 +25,531, 12 losing months totalling −$7,320 (v2.16: −$9,138); breakout square-offs
144 (−$11.4k) → 37 (−$1.4k). Sim (cent 1 lot, Oct 2024 – Sep 2026): v2.18 +413,962, drop 34.9k, low 72.2k.

**Strategy Tester v2.19** (same setup): **+$42,376, PF 1.40, max drop $3,383, 11 of 33 months losing (7 in 2024)**;
reversals +$2,877 (v2.18 +$1,017), legs +$1,940 (+$650); low $2,480. Sim, v2.19 settings, DZ TP buffer: 1.0 +446,168 /
**2.0 +471,828 (chosen, steadier)** / 3.0 +476,499 / 4.0 +459,712 / 5.0 +462,041. Reversal TP at the DZ line instead of
the pivot: +444,448 (rejected).

**Simulation on clean Trial12 data** (`sim/build_t12.py`; matches the tester: 96 % of entries, yearly P/L within a
few hundred $): cent 1 lot, Oct 2024 – Sep 2026 — v2.15 +293,335 (drop 27.1k, 7 losing months) · v2.16 +388,152
(drop 36.7k, 5) · **v2.17 (v2.16 + trap off) +399,953 (drop 36.9k, 5 losing months, −56.0k)**. $5k 0.08/0.10,
Jan 2024 – Sep 2026: v2.16 trap on +$36,433 (low $1,816) vs **trap off +$37,791 (low $2,395)**.

**v2.22 on Real7 data (3 Oct 2026) — the new base for further tests.** Exness REAL account (Exness-MT5Real7,
XAUUSD) via `sim/build_real7.py`: 163 M real ticks 1 Oct 2024 → 2 Oct 2026, so the sim runs on **real ticks for the
whole period** (Trial12 had real ticks only from 2026). Bars match Trial12 almost exactly (median close diff 0.00,
p99 0.27; gaps are holidays only, no bad 00:00 candles). Sim M1 file = Trial12 bars before 1 Oct 2024 (warm-up only) +
Real7 bars. Re-run of v2.22 on Trial12 reproduced +543,577 exactly.

| v2.22, Oct 2024 → 2 Oct 2026 | Trial12 (M1-generated ticks till 2025) | **Real7 cent 1 lot** | **Real7 standard $5k (0.08/0.10, comm 3.5, real spread)** |
|---|---|---|---|
| Net | +543,577 | **+526,260** | **+$51,558** |
| Worst drop (closed trades) | 33,516 | 33,558 | $3,322 |
| Lowest balance | 75,993 | 76,657 | $4,907 |
| Losing months | 4 (−29,155) | 5 (−32,489) | 5 (−$3,122): Dec 24, Jan 25, May 25, Aug 25, Jul 26 |
| Oct–Dec 2024 / 2025 / 2026 Jan–Sep | 46,177 / 171,820 / 325,581 | 38,141 / 150,111 / 336,481 | $3,738 / $15,801 / $31,932 |
| DZ / BO / REV / LEG | 330.6k / 119.0k / 69.1k / 25.0k | 308.8k / 125.1k / 63.4k / 29.0k | $31,365 / $12,815 / $5,381 / $1,997 |

Real ticks cost ~12 % in 2024-25 vs M1-generated ticks (fills were too kind); 2026 about equal. The standard sim's 2025
(+$15,801) and 2026 (+$31,932) match the v2.22 Strategy Tester (+$15,819 / +$31,127).

Floating profit per trade (Real7 standard, 3 Oct): highest $4,545 (DZ), average $156, median $87 (≈ 9 pts); REV med $50,
BO $94, DZ $102; 60 % of trades peak at $25–200. Winners keep 95 % of their peak (TP exits); 102 losers had been 1–2R up.
Earlier breakeven (3 Oct, base +$51,558, 5 losing months −$3,122) — ALL REJECTED, v2.22 kept: aligning BE 1.0R +$49,347
(−$2,694 losing months, breakouts −$2.3k) · 1.5R +$50,798 · DZ BE 1.0R +$47,168 · 1.5R +$48,033 (4 losing months but
−$5,186, drop $4,054) · 2.0R +$45,501 (7 losing months) · aligning 1.5R + DZ 1.5R +$47,274.
DZ entry timeframe (3 Oct, Real7 standard; entry + square-off on that TF): **M15 +$51,558 kept** · M10 +$39,239 (DZ +19.0k
vs +31.4k, losing months −$6,726) · M5 +$40,338.
IFVG instead of CISD (3 Oct, Real7 standard, base +$51,558, 5 losing months −$3,122) — ALL REJECTED, CISD kept. IFVG =
close through a whole 3-candle FVG; "strict" = gap formed at/after the setup start (first touch / breakout close),
"loose" = any gap of the last ~2 h; IFVG candle must also close beyond the level/zone; SL/TP unchanged. Aligning
(DZ = v2.22): entry IFVG M5 +$36,384 · loose M5 +$38,333 · strict M3 +$44,682 · **loose M3 +$42,746 (only 3 losing
months −$2,565, but drop $4,133, BO +4.8k vs +12.8k)** · strict M1 +$47,032 (2,288 trades, 2025 +9.8k) · loose M1
+$42,809. Exit by opposite IFVG instead of the CISD square-off: aligning +$51,570 (6 losing months), DZ +$51,038 — a
wash. DZ entry (aligning = v2.22): IFVG M15 +$29,199 (151 DZ trades) · loose M15 +$28,419 · M10 +$21,862 · loose M10
+$21,789. IFVG entries are late/rare and miss clean breakouts. Untested idea: loose M3 IFVG for reversals only.
**v2.23 on Real7 (3 Oct, standard $5k, Oct 2024 → 2 Oct 2026): +$52,162, drop $3,322, low $4,907, 3 losing months
(−$2,428: May 25, Aug 25, Jul 26)** vs v2.22 +$51,558 / 5 (−$3,122) — the quiet filter skips Dec 24 – Jan 25.
Trap recheck on it: trap on +$52,346 (+$184 only by knock-on effects); the 67 trap trades themselves −$135, 27 % win →
stays OFF.

**Dual ATR Trail flip instead of CISD (3 Oct 2026, v2.23 sim, Trial12 $5k, Jan 2024 – Sep 2026) — ALL REJECTED, CISD kept.**
Trail = dual ATR lines ATR(2)×2 (fast) + ATR(300)×2 (slow); DUAL flip = close above/below both lines; PART flip = close across
the fast line. Flip exit = opposite flip closes the trade (replaces the CISD square-off; SL/TP unchanged).
Aligning slot (v2.23 +$19,638, drop $2,005, 9 losing months): M5 CISD entry + DUAL exit +$13,210 · M3 CISD + DUAL exit
+$11,351 (drop $1,113, 8 losing) · CISD + PART exit +$9.6–10.7k · DUAL entry + CISD exit M3 +$9,120 / M5 +$5,024 (7 losing)
· flip entry + flip exit +$0.5–4.2k. DZ slot (v2.23 +$34,452, drop $3,289, 8 losing): M15 CISD entry + DUAL exit +$18,428 ·
+ PART exit +$14,670 · M15 flip entries −$0.5k to −$1.6k · M5 flip entries +$0.5k to +$8.4k (DUAL/CISD drop $6,552).
Flip entries come late (the move is already made); flip exits cut the winners that reach the level/pivot TP.

## 5b. Second clean-data batch (1 Oct 2026, base v2.20 +471,828, drop 35.1k, 5 losing months −51.2k)

Adopted in v2.21: DZ SL buffer 6 (+513.1k; 5 +503.1k, 8 +503.4k, 10 +513.6k), Monday reversal block (+477.1k, milder
months), London aligning block (+461.8k, losing months −39.6k); all three +501.7k, drop 32.9k, 4 losing months −39.2k.
Confirmed (keep): DZ trigger M15 (M5 +380.6k, M30 +321.6k), DZ SL buffer 2 worse, capped-DZ 1R rule on (+457.5k off),
aligning TP buffer 1.0 (0.5 +469.5k, 2.0 +447.2k), CISD tolerance 0.7 (0.5 +470.5k, 0.9 +456.8k), partial 7R (off
+478.6k ≈, 5R +456.0k), breakout auto square-off on (off +470.4k), touch buffer 0 (1.0 +446.5k, drop 41.7k), breakout
validity 48 (24 +439.6k, 96 +484.3k but drop 37.1k), DZ London block (+438.6k, drop 27.8k). Exit / filter ideas (2 Oct, base v2.22 cent +543,577, drop 33.5k, 4 losing months −29.2k) — ALL REJECTED:
- Exit when a new opposite aligning level appears + opposite CISD (1 h): M5 +486,274, M10 +528,561, M15 +538,191, M15 2 h
  +521,874 — cuts winners (breakouts +119k → +81k with M5).
- Basket close ($5k account, 0.08/0.10; v2.22 sim +$50,161): close all when 2 pos ≥ $400 / 3 pos ≥ $500 → +$37,345
  (drop $4,352 vs $3,322); 300/400 +$31,477; 500/600 +$39,367. Keep one (most in profit or oldest), close the rest
  (400/500) +$44,745 / +$44,568, lowest $1,948 vs $2,612; 500/600 +$46,583.
- Exit on touch of the next opposite aligning level: any +494,636; only levels ≥ 1R away +526,272 (reversals/legs capped).
- DZ filter that lifts when M10/M15 closes back inside the zone (inner edge) or inside the outer line: +533,149 /
  +527,814 / +523,894 / +527,641 — reversals gain but breakouts/legs lose, losing months −40k to −46k. The filter stays
  one-way for the whole session (it misses spikes that fully reverse, e.g. 2 Oct 18:00 4227 → 4134).
New-level trades (2 Oct): a NEWLY confirmed aligning level counts as touched for 12 M5 candles (6 tested too), first
opposite M5 CISD → trade, SL level ± 0.5 (cap 20). TP aligning level +540,079 (drop 42.8k vs 33.5k, breakouts +119k →
+81k: the slot is busy), TP pivot +505,546, 1:1 +484,078, 1:2 +497,100, 6-candle window +485,524 — REJECTED. A level is
only known after its swing is confirmed (M10: 50 min), by when price has usually moved on.
Breakout priority (2 Oct): a qualifying same-direction breakout closes the open reversal (+ leg) and opens itself:
+537,545 (losing months −35.1k vs −29.2k); with new-level trades +513,047 — REJECTED (current leg + breakeven is better).
DZ reversals (2 Oct, base v2.22 +543,577, 4 losing months −29.2k): touch of a zone + M15 LuxAlgo CISD back out of it,
SL zone edge ± 2/4/6, TP aligning level (or 1:1-1:3). Alone they LOSE (−63k/−16k/−8k); with breakouts best = SL ± 4,
TP level +561,002 but losing months −59.6k (twice as deep), lowest 71.9k, and +81.6k of it is ONE month (Mar 2026) —
the other 23 months −52.6k. Fixed-RR TPs far worse. REJECTED (DZ slot stays breakouts only).
Reversal SL (2 Oct, base v2.21 +501,667): whole touch run for all reversals +543,577 (losing months −29.2k) — ADOPTED v2.22;
failed breakouts only: whole run +535,152 / level ± 4 +519,535 / level ± 2 +525,852; all reversals level ± 4 +536,971 (5 losing
months). Not re-tested: touch expiry,
M3 fallback, loss-cutting variants (all clearly worse on the old data). Volatility fix: parked by the user.
Buy + sell open together (aligning vs DZ slot): 130 times in the v2.21 tester run, those trades net +$5,677;
blocking opposite entries across slots estimated −$881 with the same max drop → not adopted (user: "let it be").

## 5a. Decisions re-checked on CLEAN data (1 Oct 2026, cent 1 lot, Oct 2024 – Sep 2026, base = v2.16 +388,152, drop 36,676, 5 losing months)

| Change | Net | Drop | Losing months | Verdict |
|---|---|---|---|---|
| **Trap OFF** | **+399,953** | 36,866 | 5 (−56.0k vs −60.8k) | **adopted in v2.17** |
| Session TP off | +399,951 | 36,676 | 5 (−63.9k) | keep (noise) |
| Extra leg off | +382,982 | 36,676 | 5 (−49.9k) | keep (noise) |
| Trap off + leg off / + session TP off / all three | +385.9k / +408.3k / +385.2k | 37.2k / 37.0k / 38.1k | 5 | no clear gain |
| Aligning SL cap 30 / 15 | +465.7k / +333.1k | 53.5k / 43.4k | 6 / 6 | keep 20 |
| Breakeven 1R / 3R | +372.9k / +400.7k | 37.2k / 41.2k | 8 / 5 | keep 2R |
| Night block off | +400.5k | 41.8k | 5 | keep on |
| Breakout min TP 0 or 1R / 2R | +396.2k / +382.6k | 39.3k / 40.5k | 5 | keep 1.5R |
| Breakouts off / reversals off | +274.6k / +368.0k | 35.9k / 34.8k | 4 / 7 | keep both |
| DZ filter off | +341.3k | 43.0k (38.6 %) | 6 | keep on |
| DZ SL cap 50 / 100 | +362.5k / +348.5k | 36.4k / 41.7k | 5 | keep 75 |
| DZ TP 1:1 / 1:1.5 / 1:2 / level-or-1R | +349k / +358k / +355k / +394k | 47-71k | 6-9 | keep nearest level |
| DZ CISD: LuxAlgo entry + AlgoAlpha exit (v2.16) vs AlgoAlpha | +388.2k vs +293.3k | 36.7k vs 27.1k | 5 vs 7 | **adopted** |

## 5. Every decision and the test behind it — ORIGINAL table (inflated data, history only)

| Topic | Chosen | Alternatives tested (2-year net) |
|---|---|---|
| DZ trigger | M15 | M30 +547k, M5 +589k (live +648k) |
| DZ SL buffer | 4 | 2 +632k, 6 +647k |
| DZ SL limit | cap 75 | cap 50 +572k, cap 100 +631k, skip>75 +579k |
| Capped DZ needs 1R | on | off +652k (+4k, noise) |
| TP buffer | 1.0 | 2.0 +604k (older test 3/4/5 all worse) |
| DZ target | nearest level | 1:1 +534k, 1:1.5 +491k, 1:2 +477k, 1:3 +418k, level-or-1:1 +558k |
| DZ filter | on | off +553k, drop 12.5 % |
| Night reversal block 01:30–05:30 | on | off +480k, drop 26 % (reversals −10.7k) |
| Monday reversal block | off | on: same profit, drop 10.6 % |
| London no-entry 12:30–18:30 | off | all +566k, DZ-only +570k, aligning-only +644k |
| DZ TP for reversals (Z1/Z3) | off | +636k |
| Extra leg (aligning) | in profit + 1st to BE | any-time +656k (drop 14 %), cover-at-BE worse, basket close worse, half-size worse (old zones) |
| Extra leg on DZ slot | off | +661k but drop 15 % / 27 % |
| Session TP | reversals, nearest (S2): +667.6k, drop 7.0 %, 0 losing months — chosen for consistency + lowest drop, not just net | S1 prev-session only +666.5k (drop 9.7 %); on breakouts too +531k (2 losing months) |
| Aligning TFs | include M3 | v2.10-era on old zones: with M3 +581.7k (drop 13.4 %), without M3 +552.9k (drop 20 %) |
| Confirmation (aligning slot alone, 0.08/0.10 lot, old zones) | M5 CISD +26.2k | M5 candle close +20.6k, M15 CISD +14.2k, M15 close +27.2k (2× the drawdown) |
| M3 fallback when M5 CISD closes on wrong side | no | only 8–20 trades, slightly worse |
| Touch expiry | none | 6/12/24/48 candles all worse |
| Loss cutting (2026-09-30, v2.14) | none | BO BE at 1R +641.5k; DZ BE at 1R +658.3k (Mar-26 losing); DZ BE at +20 pts +626.8k (Mar-26 −17k); BO close on any opposite M5 CISD +527.8k; half off at 1R: aligning +602.2k, DZ +653.9k, both +588.5k |
| Trap trade (v2.15) | T6: square-off → opposite entry, SL level ± 4, TP 1:2, only while the trade is open | 42 traps / 2y, 43 % win, +15,000; buffer 0.5 +671.1k, 2 +673.5k, TP at level +681.5k, +1h after SL/BE +683.5k (adds Mar-25 losing month on $5k), DZ traps −7k (+660.8k), traps from 2-TF levels +689.2k but drop 7.2 % and recent period −456; $5k USD 0.06: +40,223 → +41,169 |
| Extra traps from higher-TF levels (watch-only reversal + breakout setups, M5 CISD, T6 SL/TP, on top of v2.15) | off | M15–H4 2+ TFs +678.4k (drop 7.4 %, Mar-25 −697); M15–H4 3+ TFs +681.7k; M30–H4 2+ TFs +682.4k; M30–H4 3+ TFs +685.1k (drop 7.2 %); failed-breakout traps lose in every set (14–22 % win) |
| Close on aligning level break against the trade (M5 close, any Minor/Major level) | off | any level: aligning +662.5k, DZ +633.1k (drop 10.2 %), both +628.0k; in-profit levels only: aligning +654.7k, DZ +662.2k, both +649.3k |
| Replay back-fill fix ("A") | not done (user: ignore for now) | — |
| Liquidity sweeps (aligning / single-TF Major / HTF candle, M1–M5 CISD / green candle) | rejected | 300+ combos, none profitable (best H4 M5 1:3 +895) |
| Cap vs skip, 20-pt cap (aligning) | cap 20 | skip worse (v2.02 era) |

## 6. BTCUSD (clean data, 3 Oct 2026) — tester EAs `V6S_ICT_EA_BTC_tester_v1.00/1.01/1.02.mq5`

Data: Exness **Real7 BTCUSD** (`.hcc` 2022-25 cleaned: 959 bad 00:00 records dropped; 2026 real ticks, 27.4 M).
Sim on cent terms: 2.00 BTCUSDc lots, start 77,581 USC, spread $10 on bars, swap long −18.9 USC/lot/day, **one zone
session per server day** (BTC is 24/7 → the H1-gap rule never splits). Settings = gold × 35.
- **Works:** DZ breakouts on **M30 with AlgoAlpha** (LuxAlgo and M15 worse); aligning **reversals with a stop ≥ 300**
  (+31.3k, drop 10.1 %, 5/24 losing months; plain reversals +18.3k, 10/24). **Doesn't:** aligning breakouts (−32k to
  −51k), extra legs (unstable), trend/time filters on top of breakeven, weak-breakout filter, BE on a 1:1 TP.
- DZ fixes found by digging the trades: **max 1 DZ trade per IST day**, **no DZ square-off**, **London block**,
  **breakeven** (31 % of 1:2 losers were first 1R in profit).
- **System C** (DZ 1:1.5, BE 1R) + reversals: **+151,192 USC, drop 9.1 %, 4/25 losing months**.
  **System E** (DZ 1:2, BE 1.5R) + reversals: **+201,127 USC, drop 15.3 %, 5/25** (slots simulated separately, summed).
- The tester EA defaults to System C (`DZFixedRR 1.5`, `DZBreakevenR 1.0`); System E = `DZFixedRR 2.0`,
  `DZBreakevenR 1.5`. Lots 0.02 BTCUSD = the sim's 2.00 BTCUSDc (test with a 776 USD deposit to compare 1:1).
- ETH / USOIL results (below in `docs/claude_context/project_v6s_ict_btc.md`) are still on the OLD faulty data.
- **Tester v1.00 (System C)**, Trial12 BTCUSD, Jan 2024 – Oct 2026, $5k, 0.02 lot: **+$1,336, PF 1.35, drop 11.0 %,
  8/33 losing months**; from Oct 2024 +$1,387 vs sim +$1,512 (92 % match). Jan–Sep 2024 flat (−$84). v1.01 = System E
  defaults (tester run pending). M15 DZ trigger re-tested: much worse for both C and E.
- **Weekends (IST):** Sunday DZ trades win (sim 9/10, tester 7/8; best 18:00–24:00 IST); "Saturday" DZ trades are really
  Friday's US night (Sat 00:00–05:30 IST) and lose (C 17/24 losing months). LuxAlgo M15 Saturday +34.7k / 2023–26 but
  17/31 losing months — not consistent.
- **Extending back to Jan 2023 exposed the real weakness:** System C lost 2023 (−18.7k) and H1 2024 (−31.9k), drop 82 %.
  Cause = DZ entries 00:00–05:30 IST (−29k 2023, −33k H1 2024: late, extended, bigger stop vs zone). Best windows
  18:30–21:00 IST (US open, +80k) and 09:00–12:30 (80 % win).
- **Tester v1.02 = the "consistent" setup:** DZ entries only 05:30–12:30 + 18:30–21:00 IST, no Saturday, TP = nearest
  aligning level (1:1 if under 1R), BE 1R; reversals unchanged. Sim Feb 2023 – Sep 2026 (44 months, slots summed):
  **+121,443 USC, drop 17.2 %, 10/44 losing months (worst −4,793), every year up** (+7.7k / +34.2k / +38.2k / +41.4k)
  vs v1.00 setup +87,222, drop 82 %, 19/44. Windows were found on the same data → tester run is the check.
  Other ranked variants: C 1:1.5 + windows + rev +135.6k, 14.6 %, 13/44; C no-00:00–05:30 + rev +152.6k, 19.4 %, 13/44.
  sim: `s_dz.py ... MaxDay=1 SQ=0 DZBER=1.0 NoLondon=1 FriNight=2 Win=330-750/1110-1260`, book `LVL>=1R`.
- **Tester v1.02 result** (Trial12, Jan 2024 – Sep 2026, $5k, 0.02 lot): +$937, PF 1.49, drop 2.5 % (v1.00 11 %), 8/33
  losing months, every year up (+236 / +307 / +394); DZ +$734, reversals +$203; ≈ 82 % of the sim.
- **v5.00's OB-retest slot on BTC (3 Oct 2026) — REJECTED.** Rules ×35 (H2/H1 OB first retest → M5 LuxAlgo CISD ≤ 4 h,
  SL OB ± 17.5 cap 700, TP 2R, height < 700 and < 0.25 × ADR10, stop ≥ 175, no Friday, one at a time), BTC tick volume
  from Real7 ticks (2023–25) + 2026 real ticks: Feb 2023 – Sep 2026 **−58,146 USC, drop 93 %, 26/44 losing months**.
  Every per-signal R/trade is negative for H2/H1 × LuxAlgo/AlgoAlpha × 1R/2R/3R; best variant (TP = aligning level
  beyond 2R) −1,353 with 21/44 losing; only 2026 is positive. On BTC the v5.00 combination = tester v1.02 (no OB slot).
  Why (4 Oct): after a BTC OB retest price reaches +1R before the SL 47 % (gold 53 %) and +2R 31 % (gold 39 %, break-even
  33 %) — no subset (TF, H4/H1/M15 trend, overlap, year, IST hour, CISD delay, stop size) clears break-even. The chart
  looks better because OB_Detector deletes broken OBs (only survivors stay drawn).
- **OB stop-hunt / trap variants on BTC (4 Oct) — REJECTED.** Feb 2023 – Sep 2026, one at a time, 2.00 lots: wider stop
  (OB edge − 0.5 / 1.0 × OB height) −27k to −183k; **sweep & reclaim** (OB wicked through, then an M5 LuxAlgo CISD closing
  back beyond the edge, SL = sweep extreme ± 17.5) on small OBs: +0.06R/trade ± 0.043 (not significant), TP2R +33.3k but
  22/44 losing months, 2025 −9.5k; added to v1.02: TP1R +140.9k but drop 13.4k → 24.0k, losing months 10 → 10; TP2R 16/44.
  **Trap** (trade the OB break with an opposite CISD, SL beyond the failed bounce) TP3R +29.8k but 2024 −47.6k, drop 91k;
  with v1.02 20/44 losing. v1.02 stays without any OB slot.

## 7. Live history so far (cent account, 1.00 lot)

| Opened (IST) | Trade | Result |
|---|---|---|
| 28 Sep 08:05 | aligning breakdown SELL 4207.92 | TP +1,579.20 |
| 28 Sep 10:45 | DZ SELL 4192.12 (TP 0.2 from entry → led to v2.06) | −0.60 |
| 28 Sep 11:30 | aligning breakdown SELL 4179.16 | TP +3,773.30 |
| 28 Sep 21:05 | aligning SELL 4122.01 (touch back-filled by the startup replay) | SL −2,000.00 |
| 28 Sep 21:45 | DZ SELL 4117.37 (capped, TP 0.08R → led to v2.10) | TP +573.80 |
| 29 Sep 06:05 | aligning BUY 4125.50 | TP +3,440.40 |
| 29 Sep 20:25 | breakout BUY 4167.76 | auto square-off −1,194.70 |

Known live quirks: (1) the startup replay uses the CURRENT aligning levels for earlier candles, so a restart can
create a touch before its level existed (fix "A" proposed, user said ignore for now). (2) Weekly-open / holiday gaps
can fill SL/TP far away.

## 8. Where things are

- EA source: `v6s_ict/V6S_ICT_EA_*.mq5` (all versions); indicator snapshot `v6s_ict/Dynamic_Zones_CISD_MajorMinor.mq5`;
  live indicator source `mql5/Dynamic_Zones_CISD_MajorMinor.mq5`; older guide `v6s_ict/V6S_ICT_v1.55_HOW_IT_WORKS.md`.
- Simulators + data: `v6s_ict/sim/` (see its README; run `python ticks_pack.py unpack` first).
- Previous assistant's notes for the whole repo: `docs/claude_context/` (MEMORY.md = index).
- Git branch: `add-ob-state-publisher-2` (not merged to `main`).

## 9. Open items / ideas not done

- **Volatility fix:** v2.23 adds the quiet-market entry filter (MinADR 30) — tester check pending. Scaling stops/buffers to ATR is still untested.
- **Breakout auto square-off:** −$11.4k in the tester — test removing it for breakouts.
- Time filter 15:00-17:00 IST for breakouts/DZ; trend filter for counter-trend sells.
- Consider a smaller live lot (e.g. 0.60 cent) until the volatility fix exists (a 2024-like market could cost ~60 %).
- Redo BTC/ETH/USOIL data with the fixed parser if those come back.
- Replay back-fill fix "A" (restart can create touches before a level existed) — parked by the user.
- Possible test: auto square-off also on an opposite CISD closing back into the DZ zone (from the 29 Sep trade).
