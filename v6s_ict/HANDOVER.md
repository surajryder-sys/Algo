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

## 6. Other instruments (sim only, nothing built) — ⚠ built with the same flawed `.hcc` parser, re-do before relying on them

- **BTCUSD** (Exness data Oct 2024–Sep 2026, 2.00 BTCUSDc lots, settings ×35–×50): only **DZ-only on M30** works.
  Best: DZ only, M30, ×35 (SL buffer 140, cap 2,625, TP buffer 35), **fixed 1:1**, **London entries off**:
  +115,983 USC / 2y, drop 14.8 %, 6/24 losing months. Aligning trades ≈ 0 or negative on BTC.
  NB: BTC trades 24/7 → the gap-session zone rule never splits sessions; the crypto sims still use D1 zones.
  Building it would need: DZ trigger-TF input, a switch to turn off aligning reversals, fixed-RR DZ TP, London window.
- **ETHUSD** (40 ETHUSDc lots, ×1.75–×3.5): DZ-only M30 with the **nearest-level** target: +85,014 / 23 months,
  drop 22.5 %; full EA loses in the recent period.
- **USOIL** (3.30 lots, ×0.03, no weekend carry): does **not** work (DZ loses, drawdowns > 100 %).

Details: `docs/claude_context/project_v6s_ict_btc.md`.

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

- **Volatility fix (main weakness):** scale stops/buffers/caps to ATR, or cut lots when the daily range is small.
- **Breakout auto square-off:** −$11.4k in the tester — test removing it for breakouts.
- Time filter 15:00-17:00 IST for breakouts/DZ; trend filter for counter-trend sells.
- Consider a smaller live lot (e.g. 0.60 cent) until the volatility fix exists (a 2024-like market could cost ~60 %).
- Redo BTC/ETH/USOIL data with the fixed parser if those come back.
- Replay back-fill fix "A" (restart can create touches before a level existed) — parked by the user.
- Possible test: auto square-off also on an opposite CISD closing back into the DZ zone (from the 29 Sep trade).
