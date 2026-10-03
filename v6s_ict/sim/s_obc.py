"""Sim of V6S_ICT_3.14 -- multi-timeframe ORDER BLOCK CONFLUENCE (v6s_ict_3/V6S_ICT_3.14.mq5).

  OBs: OB_Detector_v1.07 rules (volume pivot 5, wick mitigation) on H4 H2 H1 M30 M15 M10 M5 (M3 optional); only the
       indicator's 3 newest ACTIVE OBs per side per TF are visible (its EA slots).
  ARM: a closed M1 candle overlaps >= MinOBs active bullish OBs from different TFs (low <= top and high >= bottom),
       none of them used before; while armed more touched OBs join and the leg low (since the touch) is tracked.
  ENTRY: a later LuxAlgo (CisdType=lux) bullish CISD on M3/M5/M10 (its candle opens after the touch candle closed);
       ignored while a trade is open (setup stays armed).
  SL: smallest-edge rule -- any touched OB < SmallOB (10) -> lowest bottom of those - 0.5; else leg low - 0.5.
  TP = RR x risk. Setup off after 48 M5 candles or an M5 close below the lowest touched OB bottom. Sells mirrored.
  One position at a time; each OB used once.

  Align=1: ALIGNED-OB mode -- an OB only counts if its zone contains an aligning level of its type (bull OB: an
       aligning SUPPORT, bear OB: an aligning RESISTANCE; same price on >= AlignMinTFs (2) of AlignTFs, Major or Minor,
       v2.21's Major/Minor port, pivot 5); use with MinOBs=1 (one aligned OB touch arms the setup).
  HTFNeed=H4,H2,H1: a setup only arms if at least one touched OB is from one of these timeframes.
    python s_obc.py RR=2 [Start=2024-10-01] [End=2026-09-25] [MinOBs=2] [CisdTFs=M3,M5,M10] [CisdType=lux] [TrOut=f.json]
Data (default): the Exness REAL account (Real7) -- m1_real7_tv.npy + ticks_real7.npz from build_real7.py, real ticks
for the whole period, real spreads (raw-spread account: median 0.04). Comm = round-turn commission in points
(0 = not included). P/L in POINTS (= $ per 0.01 lot). TickSrc=mixed uses the old m1_2y_tv.npy + ticks.npy instead."""
import sys, json, numpy as np, datetime as dt
from ohlc import synth_ticks

P = dict(RR=2.0, Start='2024-11-01', TickSrc='real7', Comm=0.0, End='2026-09-25', MinOBs=2, OBTFs='H4,H2,H1,M30,M15,M10,M5', CisdTFs='M3,M5,M10',
         CisdType='lux', Tol=0.7, LuxMaxLen=100, SmallOB=10.0, OBBuf=0.5, LegBuf=0.5, ValidM5=48, Len=5,
         MinSpread=0.0, Data='m1_real7_tv.npy', TrOut='', Align=0, AlignMinTFs=2, AlignTFs='H4,H2,H1,M30,M15,M10,M5', HTFNeed='')
for a in sys.argv[1:]:
    k, v = a.split('='); P[k] = type(P[k])(v) if not isinstance(P[k], str) else v
SEC = dict(M1=60, M3=180, M5=300, M10=600, M15=900, M30=1800, H1=3600, H2=7200, H4=14400)
ts = lambda s: int(np.datetime64(s).astype('datetime64[s]').astype(np.int64))
START, END, REAL_FROM = ts(P['Start']), ts(P['End']), ts('2026-04-01')
IST = dt.timedelta(hours=5, minutes=30)
ist = lambda t: (dt.datetime.fromtimestamp(int(t), dt.timezone.utc) + IST).strftime('%Y-%m-%d %H:%M')

m1 = np.load(P['Data'])
MT, MH, ML = m1['time'], m1['high'], m1['low']

def agg(sec):
    b = (MT // sec) * sec; idx = np.flatnonzero(np.diff(b)) + 1
    st = np.concatenate([[0], idx]); en = np.concatenate([idx, [len(b)]])
    return dict(time=b[st], open=m1['open'][st], close=m1['close'][en - 1], high=np.maximum.reduceat(MH, st),
                low=np.minimum.reduceat(ML, st), tv=np.add.reduceat(m1['tv'], st))

# ---------- OB zones per TF (OB_Detector_v1.07) ----------
def zones_for(ti, tf):
    sec = SEC[tf]; b = agg(sec); T, H, L, V = b['time'], b['high'], b['low'], b['tv']; n = len(T); N = P['Len']
    os_ = 0; act = {0: [], 1: []}; out = []
    for i in range(N, n):
        c = i - N; up = H[i - N + 1:i + 1].max(); lo = L[i - N + 1:i + 1].min()
        if H[c] > up: os_ = 0
        elif L[c] < lo: os_ = 1
        if c >= N and all(V[c - j] < V[c] for j in range(1, N + 1)) and all(V[c + j] <= V[c] for j in range(1, N + 1)):
            hl2 = (H[c] + L[c]) / 2; side = 0 if os_ == 1 else 1      # 0 bull, 1 bear
            z = dict(t=ti, tf=tf, side=side, top=hl2 if side == 0 else H[c], btm=L[c] if side == 0 else hl2,
                     id=(ti, int(T[c])), formed=int(T[i]) + sec, mit=1 << 62)
            act[side].insert(0, z); out.append(z)
        ct = int(T[i]) + sec
        for z in act[0]:
            if lo < z['btm']: z['mit'] = ct
        for z in act[1]:
            if up > z['top']: z['mit'] = ct
        act[0] = [z for z in act[0] if z['mit'] == 1 << 62]; act[1] = [z for z in act[1] if z['mit'] == 1 << 62]
    return out

OBTF = P['OBTFs'].split(',')
ALLZ = []
for ti, tf in enumerate(OBTF): ALLZ += zones_for(ti, tf)
ALLZ.sort(key=lambda z: z['formed'])

# ---------- aligning levels over time (Align=1) ----------
SNT, SNS, SNR = [0], [frozenset()], [frozenset()]
if P['Align']:
    from v6sim import MajorMinor
    ev = []; MMB = {}
    for tf in P['AlignTFs'].split(','):
        b = agg(SEC[tf]); MMB[tf] = (b, MajorMinor(tf))
        ev += [(int(b['time'][k]) + SEC[tf], tf, k) for k in range(len(b['time']))]
    ev.sort()
    j = 0
    while j < len(ev):
        t0 = ev[j][0]
        while j < len(ev) and ev[j][0] == t0:
            _, tf, k = ev[j]; b, mm = MMB[tf]; mm.add(b['high'][k], b['low'][k], b['close'][k], int(b['time'][k])); j += 1
        cnt = {}
        for tf, (b, mm) in MMB.items():
            for sd, v, kind in mm.levels(): cnt.setdefault((sd, v), set()).add(tf)
        sup = frozenset(v for (sd, v), tfs in cnt.items() if sd == 'S' and len(tfs) >= P['AlignMinTFs'])
        res = frozenset(v for (sd, v), tfs in cnt.items() if sd == 'R' and len(tfs) >= P['AlignMinTFs'])
        if sup != SNS[-1] or res != SNR[-1]: SNT.append(t0); SNS.append(sup); SNR.append(res)
    print('aligning snapshots', len(SNT))

# ---------- CISD per entry TF ----------
def cisd_lux(O, C):
    cs = np.zeros(len(C), dtype=np.int8); ob = oe = None; mx = P['LuxMaxLen']
    for n in range(1, len(C)):
        bull, bear = C[n] > O[n], C[n] < O[n]; pbull, pbear = C[n-1] > O[n-1], C[n-1] < O[n-1]
        if bull and pbear: ob = (n, O[n])
        if bear and pbull: oe = (n, O[n])
        if ob is not None:
            if n - ob[0] <= mx:
                if C[n] < ob[1]: cs[n] = -1; ob = None
            else: ob = None
        if oe is not None:
            if n - oe[0] <= mx:
                if C[n] > oe[1]: cs[n] = 1; oe = None
            else: oe = None
    return cs
def cisd_aa(O, C):
    bear, bull = [], []; cs = np.zeros(len(C), dtype=np.int8); tol = P['Tol']
    for i in range(len(C)):
        if i >= 1:
            if C[i-1] < O[i-1] and C[i] > O[i]: bear.insert(0, (O[i], i))
            if C[i-1] > O[i-1] and C[i] < O[i]: bull.insert(0, (O[i], i))
        while bear:
            co, ci = bear[0]
            if C[i] < co:
                highest = max(0.0, C[ci:i+1].max()); top = 0.0; k = ci - 1
                while k >= 0 and C[k] < O[k]: top = O[k]; k -= 1
                d = top - co
                if d != 0 and (highest - co) / d > tol: bear.clear(); cs[i] = -1; break
                bear.pop(0)
            else: break
        while bull:
            co, ci = bull[0]
            if C[i] > co:
                lowest = C[ci:i+1].min(); bottom = 0.0; k = ci - 1
                while k >= 0 and C[k] > O[k]: bottom = O[k]; k -= 1
                d = co - bottom
                if d != 0 and (co - lowest) / d > tol: bull.clear(); cs[i] = 1; break
                bull.pop(0)
            else: break
    return cs
EV = []   # (close time, tf, dir, candle open, candle low, candle high)
for tf in P['CisdTFs'].split(','):
    b = agg(SEC[tf]); cs = (cisd_lux if P['CisdType'] == 'lux' else cisd_aa)(b['open'], b['close'])
    for k in np.flatnonzero(cs):
        EV.append((int(b['time'][k]) + SEC[tf], tf, int(cs[k]), int(b['time'][k]), float(b['low'][k]), float(b['high'][k])))
EV.sort(key=lambda e: (e[0], {'M5': 0, 'M10': 1, 'M3': 2}.get(e[1], 3)))
M5b = agg(300); M5C = M5b['time'] + 300; M5Cl = M5b['close']

# ---------- ticks ----------
if P['TickSrc'] == 'real7':
    z = np.load('ticks_real7.npz'); TMS = z['t'].astype(np.float64); BID = z['b'] / 1000.0
    ASK = np.maximum(BID + z['s'] / 1000.0, BID + P['MinSpread']); del z
else:
    syn = synth_ticks(m1[(MT >= START - 86400 * 40) & (MT < REAL_FROM)])
    real = np.load('ticks.npy'); real = real[real[:, 0] >= REAL_FROM * 1000]
    TK = np.concatenate([syn, real]); TMS, BID = TK[:, 0], TK[:, 1]; ASK = np.maximum(TK[:, 2], BID + max(P['MinSpread'], 0.26))
NT = len(TMS)
def first_true(fn, a, n):
    step = 2000
    while a < n:
        b = min(n, a + step); hit = np.flatnonzero(fn(a, b))
        if len(hit): return a + int(hit[0])
        a = b; step *= 4
    return -1

# ---------- event loop over M1 candles ----------
RR = P['RR']; MINOBS = P['MinOBs']; HTFN = set(P['HTFNeed'].split(',')) if P['HTFNeed'] else set()
active = {}            # (t, side) -> list newest-first
zi = 0; ei = 0; mi = 0; nextmit = 1 << 62; si = 0
setup = {0: None, 1: None}; used = set(); busy_until = 0; trades = []
first_m1 = int(np.searchsorted(MT, START - 3 * 86400))
for i in range(first_m1, len(MT)):
    now = int(MT[i]) + 60
    if now >= END: break
    # OB formations / mitigations known by now
    while zi < len(ALLZ) and ALLZ[zi]['formed'] <= now:
        z = ALLZ[zi]; active.setdefault((z['t'], z['side']), []).insert(0, z); zi += 1; nextmit = min(nextmit, z['mit'])
    if nextmit <= now:
        nextmit = 1 << 62
        for key in list(active):
            active[key] = [z for z in active[key] if z['mit'] > now]
            for z in active[key]: nextmit = min(nextmit, z['mit'])
    # 1. M1 touch
    h, l = MH[i], ML[i]
    while si + 1 < len(SNT) and SNT[si + 1] <= now: si += 1
    for side in (0, 1):
        hit = []
        for (t, sd), lst in active.items():
            if sd != side: continue
            for z in lst[:3]:
                if z['id'] in used: continue
                if l <= z['top'] and h >= z['btm']:
                    if P['Align'] and not any(z['btm'] <= v <= z['top'] for v in (SNS[si] if side == 0 else SNR[si])): continue
                    hit.append(z)
        S = setup[side]
        if S is None:
            if HTFN and not any(z['tf'] in HTFN for z in hit): continue
            if len({z['t'] for z in hit}) >= MINOBS:
                setup[side] = dict(touchT=int(MT[i]), ext=(l if side == 0 else h), obs={z['id']: z for z in hit})
        else:
            S['ext'] = min(S['ext'], l) if side == 0 else max(S['ext'], h)
            for z in hit: S['obs'].setdefault(z['id'], z)
    # 2. M5 close: expiry / break
    while mi < len(M5C) and M5C[mi] <= now:
        if M5C[mi] == now:
            for side in (0, 1):
                S = setup[side]
                if S is None: continue
                edge = min(z['btm'] for z in S['obs'].values()) if side == 0 else max(z['top'] for z in S['obs'].values())
                age = (now - S['touchT']) // 300
                if age > P['ValidM5'] or (M5Cl[mi] < edge if side == 0 else M5Cl[mi] > edge): setup[side] = None
        mi += 1
    # 3. CISD entries closing at 'now'
    while ei < len(EV) and EV[ei][0] <= now:
        ct, tf, d, copen, clo, chi = EV[ei]; ei += 1
        if ct != now: continue
        side = 0 if d > 0 else 1; S = setup[side]
        if S is None or S['touchT'] + 60 > copen: continue
        if now < busy_until: continue                                  # trade open -> setup stays armed
        e = int(np.searchsorted(TMS, now * 1000.0))
        if e >= NT: continue
        entry = ASK[e] if d > 0 else BID[e]
        small = [z for z in S['obs'].values() if z['top'] - z['btm'] < P['SmallOB']]
        if small:
            sl = min(z['btm'] for z in small) - P['OBBuf'] if d > 0 else max(z['top'] for z in small) + P['OBBuf']; why = 'smallOB'
        else:
            ext = min(S['ext'], clo) if d > 0 else max(S['ext'], chi)
            sl = ext - d * P['LegBuf']; why = 'leg'
        setup[side] = None
        for zid in S['obs']: used.add(zid)
        risk = (entry - sl) * d
        if risk <= 0 or now < START: continue
        tp = entry + d * RR * risk
        if d > 0: k = first_true(lambda a, b: (BID[a:b] <= sl) | (BID[a:b] >= tp), e, NT)
        else: k = first_true(lambda a, b: (ASK[a:b] >= sl) | (ASK[a:b] <= tp), e, NT)
        if k < 0: continue
        px = BID[k] if d > 0 else ASK[k]; xt = TMS[k] / 1000.0
        busy_until = xt
        trades.append(dict(t=now, x=xt, d=d, tf=tf, entry=float(entry), sl=float(sl), tp=float(tp), risk=float(risk),
                           pts=float((px - entry) * d - P['Comm']), win=bool((px >= tp) if d > 0 else (px <= tp)), why=why,
                           obs=sorted(f"{z['tf']} {z['btm']:.2f}-{z['top']:.2f}" for z in S['obs'].values())))

# ---------- report ----------
def stats(tr, lab):
    if not tr: print(f"  {lab:10s} no trades"); return
    eq = 0; pk = 0; dd = 0; mon = {}
    for x in sorted(tr, key=lambda x: x['x']):
        eq += x['pts']; pk = max(pk, eq); dd = max(dd, pk - eq)
        m = dt.datetime.fromtimestamp(int(x['x']), dt.timezone.utc).strftime('%y-%m'); mon[m] = mon.get(m, 0) + x['pts']
    lose = sum(v < 0 for v in mon.values())
    print(f"  {lab:10s} trades {len(tr):4d}  win {sum(x['win'] for x in tr)/len(tr)*100:5.1f}%  net {eq:+9.1f} pts  "
          f"max drop {dd:7.1f} pts  losing months {lose:2d}/{len(mon)}  avg risk {np.mean([x['risk'] for x in tr]):5.2f}")
    return mon
print(f"{'ALIGNED OB (' + str(P['AlignMinTFs']) + '+ TF levels)' if P['Align'] else 'OB confluence'}{' + HTF OB ' + P['HTFNeed'] if HTFN else ''} | RR 1:{RR:g} | {P['Start']} -> {P['End']} | data {P['TickSrc']} | comm {P['Comm']} | min {MINOBS} TFs of {P['OBTFs']} | {P['CisdType']} CISD on {P['CisdTFs']}")
mon = stats(trades, 'ALL')
for lab, f in (('BUY', lambda x: x['d'] > 0), ('SELL', lambda x: x['d'] < 0), ('SL smallOB', lambda x: x['why'] == 'smallOB'),
               ('SL leg', lambda x: x['why'] == 'leg'), ('2026', lambda x: x['t'] >= ts('2026-01-01'))):
    stats([x for x in trades if f(x)], lab)
for tf in P['CisdTFs'].split(','): stats([x for x in trades if x['tf'] == tf], 'CISD ' + tf)
if mon: print('  months: ' + ' '.join(f"{k} {v:+.0f}" for k, v in mon.items()))
if P['TrOut']: json.dump(trades, open(P['TrOut'], 'w'), default=float)
