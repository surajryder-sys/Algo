# V6S-ICT — full handover (state as of 30 Sep 2026, IST)

Written for a teammate / a new Claude chat picking this up with no prior context. Read this first, then
`v6s_ict/sim/README.md` (how to re-run every test) and `docs/claude_context/` (the previous assistant's saved notes
about the whole repo and the user's working preferences).

---

## 0. Working rules the user (Suraj) expects

- **Report every time in IST (UTC+5:30).** Exness server time = UTC. Gold's daily session starts 03:30 IST (22:00 UTC).
- **Every EA change = a NEW versioned file** `<EA>_v<x.yy>.mq5`; never overwrite an old one (one exception the user
  explicitly asked for: a display-only panel fix was overwritten in place in v2.09).
- **Three copies of each version:** `V6S_ICT_EA_vX` (base lots), `V6S_ICT_EA_cent_vX` (3×: 0.24/0.30/0.30),
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
| **MetaTrader 5** | `C:\Program Files\MetaTrader 5` · data `...\Terminal\D0E8209F77C8CF37AD8BF550E51FF075` | **Exness REAL cent 263602422** (Exness-MT5Real37, USC). Balance 77,581 USC when the EA went live (28 Sep 04:14 IST) | **LIVE**: `V6S_ICT_EA_cent_1lot_v2.14` on **XAUUSDc M5** |
| **MetaTrader5-5** | `C:\Program Files\MetaTrader5-5` · data `...\Terminal\4DB333B24A74B726D7AA441A9D0137DC` | on 29 Sep it was logged into VantageMarkets-Live 7 #26518949 (USD, $2.06, no EA trades) | visual tester / compile target |

EA folder in both: `MQL5\Experts\V6S_ICT\`. Compile from the command line:
`& "<terminal dir>\MetaEditor64.exe" /compile:"<data>\MQL5\Experts\V6S_ICT\<file>.mq5" /inc:"<data>\MQL5" /log:"<log>"`.

Contract specs (cent account): **XAUUSDc 1.00 lot = 100 USC per $1**, spread ≈ 0.24–0.26, no commission, long swap
≈ −56 USC/lot/night. BTCUSDc 1.00 lot = 1 USC per $1 (spread $10). ETHUSDc 1.00 lot = 1 USC per $1 (spread $1).
USOILc 1.00 lot = 1,000 USC per $1 (spread 0.02, short swap −186 USC/lot/night).

## 2. What the EA does — v2.14 (current, live)

File: `v6s_ict/V6S_ICT_EA_cent_1lot_v2.14.mq5` (identical logic in the other two copies). Chart: XAUUSDc, any TF
(M5 recommended). All inputs at their defaults are the tested/agreed settings.

**Levels.** Major/Minor support & resistance (ZigZag, pivot period 5, ported verbatim from the indicator's
"yellow line" logic) on 8 TFs: H4 H2 H1 M30 M15 M10 M5 M3. **Aligning level** = the exact same price on ≥3 TFs,
with ≥2 of them Major.

**Slot 1 — aligning-level trades** (magic 26092701, 1.00 lot, one main trade at a time):
- *Reversal:* an M5 candle touches an aligning level (touch never expires); a LATER M5 CISD (AlgoAlpha port,
  tolerance 0.7) closes back beyond it → trade. SL = extreme after the touch ± 0.5, **capped at 20**.
  **No reversal entries 01:30–05:30 IST** (v2.11).
- *Breakout:* M5 close through a level (valid 48 candles, cancelled by a close back through), then a LATER M5 CISD
  beyond it → trade. SL = CISD swing ± 0.5, capped at 20. Only if the TP is ≥ 1.5R.
- *TP:* **reversals → nearest session target (v2.14)**: nearest beyond price of the previous gap-session's high
  (buy) / low (sell) and the current session's high/low so far (closed M5), ∓1.0; fallback = nearest aligning level
  ∓1.0. **Breakouts → nearest opposite aligning level ∓1.0.** Either way: if the target is < 1R from entry → 1:1
  (v2.06). Re-checked every M5 candle.
- *Management:* breakeven at 2R; partial close at 7R keeping 0.10 runner; **auto square-off** when an opposite M5
  CISD closes back beyond the trade's level; an opposite setup closes & reverses.
- *Extra leg (v2.12, magic 26092731):* first trade **in profit** + new same-side setup → one extra leg (same lot,
  setup SL capped 20, TP 1:2) and the first trade's SL → breakeven. Max one leg; it closes with the first trade on
  an opposite setup / square-off.
- *DZ filter:* an M5 close beyond the outer zone line blocks the opposite direction for the rest of the session.
- Off by default: brake, M10-after-SL, news filter. Spread filter 0.50; no entries in the first 60 min of the week.

**Slot 2 — Dynamic Zones (DZ) breakouts** (magic 26092721, 1.00 lot, independent of slot 1):
- Zones per **gap-delimited session** (v2.13, same as `mql5/Dynamic_Zones_CISD_MajorMinor.mq5`): a new session
  starts wherever consecutive H1 bars are > 1 h apart; anchor = session's first H1 open; Z1/Z3 = open ± ½ avg range
  of the previous 5 complete sessions, Z2/Z4 = same with 10. Resistance zone = Z1–Z2, support zone = Z3–Z4.
- Entry: an **M15 CISD** candle closing beyond the zone (above resistance → buy, below support → sell).
- SL = far zone line ± 4.0, **capped at 75**; a capped trade needs TP ≥ 1R (v2.10). TP = nearest aligning level
  ∓1.0 fixed at entry (1:1 if none; skipped if < 1 pt away). An opposite DZ setup squares it off.

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
| **v2.14** | **session-high/low TP for reversals** |
| v3.00/3.01 | hedge-basket experiments (not used) |

## 4. Headline backtest results (Python sims, see `sim/README.md`)

Gold, cent account, 1.00 lot, start 77,581 USC, Oct 2024 – 25 Sep 2026 (old period on 1-min bars, Apr–Sep 2026
on real ticks), costs included:

| Config | 2-year net (USC) | Worst drop | Losing months |
|---|---|---|---|
| **v2.14 (live)** | **+667,620** | **7.0 %** | none |
| v2.13 | +648,359 | 7.7 % | Sep 2026 −142 |
| v2.12 on old D1 zones (for reference) | +657,720 | 11.9 % | May 2025 −9,126 |

v2.13 split: reversals +153,927 · breakouts +171,691 · extra legs +23,107 · DZ +299,635. v2.13 months (USC):
2024-10 +3,909 · 11 +45,103 · 12 +20,863 · 2025-01 +6,716 · 02 +21,446 · 03 +225 · 04 +47,208 · 05 +6,522 ·
06 +13,156 · 07 +31,170 · 08 +12,886 · 09 +56,814 · 10 +56,594 · 11 +10,616 · 12 +21,325 · 2026-01 +72,299 ·
02 +10,913 · 03 +23,333 · 04 +18,387 · 05 +52,109 · 06 +29,981 · 07 +26,320 · 08 +60,606 · 09 −142.

5,000 USD standard account (v2.12, comm $3.5/lot/side): 0.06 lot +39,600 (drop $1,740); 0.10 lot +65,847 (drop
$2,900). Recommended 0.05–0.06 lot for $5k.

**Caveat:** simulation only — never confirmed in the MT5 Strategy Tester; many settings were chosen on this same data.

## 5. Every decision and the test behind it (all re-checked on gap-session zones unless noted)

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
| Close on aligning level break against the trade (M5 close, any Minor/Major level) | off | any level: aligning +662.5k, DZ +633.1k (drop 10.2 %), both +628.0k; in-profit levels only: aligning +654.7k, DZ +662.2k, both +649.3k |
| Replay back-fill fix ("A") | not done (user: ignore for now) | — |
| Liquidity sweeps (aligning / single-TF Major / HTF candle, M1–M5 CISD / green candle) | rejected | 300+ combos, none profitable (best H4 M5 1:3 +895) |
| Cap vs skip, 20-pt cap (aligning) | cap 20 | skip worse (v2.02 era) |

## 6. Other instruments (sim only, nothing built)

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

- Confirm v2.14 in the MT5 Strategy Tester (visual) before scaling lots.
- Replay back-fill fix "A" (restart can create touches before a level existed) — parked by the user.
- BTC DZ-only EA (see §6) — parked by the user.
- Possible test: auto square-off also on an opposite CISD closing back into the DZ zone (from the 29 Sep trade).
