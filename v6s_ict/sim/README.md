# V6S-ICT backtest simulators (Python)

Tick-level Python replicas of the MQL5 EA `v6s_ict/V6S_ICT_EA_*_v2.xx.mq5`, used for **every** test result in
`v6s_ict/HANDOVER.md`. They are NOT the MT5 Strategy Tester — expect live results to be lower (slippage, gaps,
spread spikes, and many settings were tuned on this same data).

## Setup (once, after cloning)

```bash
cd v6s_ict/sim
pip install numpy MetaTrader5          # MetaTrader5 only needed for build scripts that read a terminal
python ticks_pack.py unpack           # data/ticks_part1..3.npz -> ticks.npy (915 MB, git-ignored)
cp data/*.npy .                       # the sims load their data from the current folder
```

## Data (in `data/`)

| File | What | Source |
|---|---|---|
| `m1_2y.npy` | XAUUSD 1-min bars, Jan 2024 → 25 Sep 2026 | Exness Real7 .hcc + real ticks (`build2y.py`) |
| `ticks_part*.npz` → `ticks.npy` | XAUUSD real ticks [ms, bid, ask], 1 Apr → 25 Sep 2026 | Exness Trial12, exported with the MetaTrader5 package |
| `btc_m1.npy` / `eth_m1.npy` / `oil_m1.npy` | BTCUSD / ETHUSD / USOIL 1-min bars | `build_data.py BTC|ETH|OIL` (Exness .hcc files) |
| `m3.npy m3t.npy m5.npy m15.npy m5h.npy m3h.npy m1_clean.npy` | older XAUUSD series used only by the V5S grid sim (`sim.py`) | — |

Periods used everywhere: **old** = `Start=2024-10-01 End=2026-04-01 TickSrc=ohlc` (synthetic ticks from 1-min
bars), **new/recent** = `Start=2026-04-01` (real ticks). Costs for the cent account: `Comm=0 MinSpread=0.26`
(gold), long swap −55.04/lot/night built in. Lot 1.00 ⇒ results are in **USC on the Exness cent account**
(XAUUSDc 1.00 lot = 100 USC per $1). For a USD standard account use e.g. `Lot=0.06 BOLots=0.06 Comm=3.5 MinSpread=0.20`.

## Scripts

| Script | Slot / purpose |
|---|---|
| `s_v6.py` | **Aligning-level slot, current** (v2.14 logic): reversals + breakouts, extra leg, night block, DZ filter on gap-session zones, session TP. Writes `TrOut` json (typ REV/BO/LEG). |
| `s_dz.py` | **DZ breakout slot, current** (gap-session zones, cap/skip, capped-1R, square-off, optional DZ leg). Several target "books" per run: LEVEL (live), LVL>=1R, 1:1 … 1:3. |
| `sessz.py` | Gap-delimited sessions + Dynamic Zones (identical to `mql5/Dynamic_Zones_CISD_MajorMinor.mq5` and EA v2.13+). |
| `v6sim.py` | Major/Minor ZigZag port (`MajorMinor`), bar aggregation `agg`, timeframes `TFS`. |
| `ohlc.py` | `synth_ticks` (o, l/h, h/l, c with spread 0.09) and `m1_from_ticks`. |
| `combine.py` | Combine the two slots into one account result (net, drop, split, months). |
| `v6run.py` | Original v2.x sim (D1 zones) — historical. |
| `v3run.py` | v3.x hedge-basket sim — historical. |
| `bt_btbo.py`, `bt_dz3.py` | BTC/ETH versions (D1 zones, `Data=`, `TradeTF=`, `RevOff=`, `Contract=`). |
| `bt_oil.py`, `bt_dzo.py` | USOIL versions (no weekend carry `WkCut`, short swap `SwapShortPerLot`). |
| `sim.py` | V5S-ICT grid EA sim (older project). |
| `build2y.py`, `build_data.py`, `ticks_pack.py` | Data builders. |

Every script takes `Key=Value` arguments that override the `P = dict(...)` defaults at the top of the file.

## Reproduce the live EA (v2.14, cent 1-lot) — expected ≈ +667,620 USC / 2 years, drop 7.0 %

```bash
B="MinTF=3 MinMajor=2 BER=2.0 PartR=7.0 TouchBuf=0.0 SLFromTouch=0 SLBuf=0.5 OppTP=1 AutoSq=1 BO=1 SLMode=cap MaxSL=20 MinRRRev=0 MinRRBO=1.5 M10AfterSL=0 Brake=0 Lot=1.0 BOLots=1.0 Runner=0.10 MinTPR=1.0 NoRevLate=1 AddLeg=1 LegWhen=profit LegBE=1 LegTP=2.0 DZF=1 DZTP=0 SessTP=2 SessScope=rev Comm=0 MinSpread=0.26"
D="Lot=1.0 BOSL=zone Modes=BO SQ=1 MinTPPts=1.0 TF=900 SLBuf=4 MaxSL=75 CapMinTPR=1.0 TPBuf=1.0 Comm=0 MinSpread=0.26"
python s_v6.py $B Start=2024-10-01 End=2026-04-01 TickSrc=ohlc MeqOut=q.json TrOut=al_old.json > al_old.txt
python s_v6.py $B Start=2026-04-01 MeqOut=r.json TrOut=al_new.json > al_new.txt
python s_dz.py $D Start=2024-10-01 End=2026-04-01 TickSrc=ohlc TrOut=dz_old.json > dz_old.txt
python s_dz.py $D Start=2026-04-01 TrOut=dz_new.json > dz_new.txt
python combine.py al_old.json al_new.json dz_old.json dz_new.json 77581
```

The four runs are independent — start them in parallel (`&` + `wait`); each takes a few minutes.
v2.13 (before session TP) = same with `SessTP=0` → ≈ +648,359.
