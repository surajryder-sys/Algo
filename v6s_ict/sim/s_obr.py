"""OB retest study (2026-10-03): HTF order block -> first retest -> LTF CISD in the bounce direction -> entry.

  OBs: OB_Detector v1.07 rules (volume pivot, length 5, wick mitigation) on H4 H2 H1 M30 M15 M10 M5.
  Retest = first touch of the zone after it formed (bullish: price <= top). Only the FIRST retest is traded.
  Entry: first CISD in the OB's direction (bull OB -> bullish CISD) on the LTF (M1 / M3 / M5; LuxAlgo Classic or
  AlgoAlpha 0.7) whose candle closes after the retest and within WinMin minutes. If the OB is invalidated (mitigated)
  before that CISD closes -> no trade. Entry at the first tick after the CISD close (buy at ask, sell at bid).
  SL: OB low - 0.5 (buy) / OB high + 0.5 (sell), capped at Cap points from entry (Cap=15 and Cap=20 both recorded).
  Every signal is evaluated on its own (no position limit) to measure the setup itself. Targets 1R / 2R / 3R and the
  nearest opposite aligning level (>= 3 TFs H4-M5, M5 Majors only) - 1.0 are all read off one tick pass: the trade
  is a win if price reaches the target before the SL; if neither within Horizon days it closes at market then.
  Records MFE (in R) before the SL, so "went in favour then came back" can be counted.
  Real7 ticks. Comm 0.07 pts per trade (= $7/lot). Output: summary tables + trade dump (TrOut) for an_obr.py.
    python s_obr.py [WinMin=240] [Start=2024-11-01] [End=2026-10-02] [TrOut=obr.json]"""
import sys, json, numpy as np, datetime as dt
from v6sim import MajorMinor
from sessz import session_zones

P = dict(Start='2024-11-01', End='2026-10-02', WinMin=240, Len=5, Comm=0.07, Horizon=5, TrOut='', OBTFs='H4,H2,H1,M30,M15,M10,M5',
         LTFs='M1,M3,M5', SLBuf=0.5, Mgmt=0, Caps='15,20', MaxSeq=1, ReWin=1440, Data='real7')
for a in sys.argv[1:]:
    k, v = a.split('='); P[k] = type(P[k])(v) if not isinstance(P[k], str) else v
SEC = dict(M1=60, M3=180, M5=300, M10=600, M15=900, M30=1800, H1=3600, H2=7200, H4=14400)
ts = lambda s: int(np.datetime64(s).astype('datetime64[s]').astype(np.int64))
START, END = ts(P['Start']), ts(P['End']); U = dt.timezone.utc

m1 = np.load(f"m1_{P['Data']}_tv.npy"); MT, MH, ML = m1['time'].astype(np.int64), m1['high'], m1['low']
def agg(sec):
    b = (MT // sec) * sec; idx = np.flatnonzero(np.diff(b)) + 1
    st = np.concatenate([[0], idx]); en = np.concatenate([idx, [len(b)]])
    return dict(time=b[st], open=m1['open'][st], close=m1['close'][en - 1], high=np.maximum.reduceat(MH, st),
                low=np.minimum.reduceat(ML, st), tv=np.add.reduceat(m1['tv'], st))
z = np.load(f"ticks_{P['Data']}.npz"); TT = z['t']; BID = z['b'] / 1000.0; ASK = BID + z['s'] / 1000.0; del z
NT = len(TT)

def first_true(fn, a, n, step=256):
    while a < n:
        b = min(n, a + step); hit = np.flatnonzero(fn(a, b))
        if len(hit): return a + int(hit[0])
        a = b; step *= 4
    return -1

# ---------- OBs (v1.07) ----------
def zones_for(tf):
    sec = SEC[tf]; b = agg(sec); T, H, L, V = b['time'], b['high'], b['low'], b['tv']; n = len(T); N = P['Len']
    os_ = 0; act = {1: [], -1: []}; out = []
    for i in range(N, n):
        c = i - N; up = H[i - N + 1:i + 1].max(); lo = L[i - N + 1:i + 1].min()
        if H[c] > up: os_ = 0
        elif L[c] < lo: os_ = 1
        if c >= N and all(V[c - j] < V[c] for j in range(1, N + 1)) and all(V[c + j] <= V[c] for j in range(1, N + 1)):
            hl2 = (H[c] + L[c]) / 2; d = 1 if os_ == 1 else -1
            q = dict(tf=tf, d=d, top=float(hl2 if d > 0 else H[c]), btm=float(L[c] if d > 0 else hl2), start=int(T[c]),
                     formed=int(T[i]) + sec, mit=1 << 62)
            act[d].insert(0, q); out.append(q)
        ct = int(T[i]) + sec
        for q in act[1]:
            if lo < q['btm']: q['mit'] = ct
        for q in act[-1]:
            if up > q['top']: q['mit'] = ct
        act[1] = [q for q in act[1] if q['mit'] == 1 << 62]; act[-1] = [q for q in act[-1] if q['mit'] == 1 << 62]
    return out

# ---------- CISD ----------
def cisd_lux(O, C):
    cs = np.zeros(len(C), dtype=np.int8); ob = oe = None
    for n in range(1, len(C)):
        bull, bear = C[n] > O[n], C[n] < O[n]; pbull, pbear = C[n-1] > O[n-1], C[n-1] < O[n-1]
        if bull and pbear: ob = (n, O[n])
        if bear and pbull: oe = (n, O[n])
        if ob is not None:
            if n - ob[0] <= 100:
                if C[n] < ob[1]: cs[n] = -1; ob = None
            else: ob = None
        if oe is not None:
            if n - oe[0] <= 100:
                if C[n] > oe[1]: cs[n] = 1; oe = None
            else: oe = None
    return cs
def cisd_aa(O, C, tol=0.7):
    bear, bull = [], []; cs = np.zeros(len(C), dtype=np.int8)
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
CI = {}
for tf in P['LTFs'].split(','):
    b = agg(SEC[tf])
    for nm, fn in (('lux', cisd_lux), ('aa', cisd_aa)):
        cs = fn(b['open'], b['close']); ct = (b['time'] + SEC[tf]).astype(np.int64)
        CI[(tf, nm)] = {1: ct[cs == 1], -1: ct[cs == -1]}
print('CISD events', {f'{k[0]}{k[1]}': len(v[1]) + len(v[-1]) for k, v in CI.items()})

# ---------- aligning levels ----------
SNT, SNS, SNR = [0], [()], [()]
ev_ = []; MMB = {}
for tf in ('H4', 'H2', 'H1', 'M30', 'M15', 'M10', 'M5'):
    bb = agg(SEC[tf]); MMB[tf] = (bb, MajorMinor(tf))
    ev_ += [(int(bb['time'][k]) + SEC[tf], tf, k) for k in range(len(bb['time']))]
ev_.sort(); j = 0
while j < len(ev_):
    t0 = ev_[j][0]
    while j < len(ev_) and ev_[j][0] == t0:
        _, tf, k = ev_[j]; bb, mm = MMB[tf]; mm.add(bb['high'][k], bb['low'][k], bb['close'][k], int(bb['time'][k])); j += 1
    cn = {}
    for tf, (bb, mm) in MMB.items():
        for sd, v, kind in mm.levels():
            if tf == 'M5' and kind == 'Min': continue
            cn.setdefault((sd, v), set()).add(tf)
    sup = tuple(sorted(v for (sd, v), q in cn.items() if sd == 'S' and len(q) >= 3))
    res = tuple(sorted(v for (sd, v), q in cn.items() if sd == 'R' and len(q) >= 3))
    if sup != SNS[-1] or res != SNR[-1]: SNT.append(t0); SNS.append(sup); SNR.append(res)
SNT = np.array(SNT)
def align_tp(d, e, t):
    q = int(np.searchsorted(SNT, t, side='right')) - 1; lv = list(SNS[q]) + list(SNR[q])
    if d > 0:
        c = [v for v in lv if v - 1.0 > e + 0.5]; return min(c) - 1.0 if c else None
    c = [v for v in lv if v + 1.0 < e - 0.5]; return max(c) + 1.0 if c else None

# ---------- context: HTF CISD (LuxAlgo) and Dynamic Zones ----------
HTFC = {}
for tf in ('M15', 'H1', 'H4'):
    bb = agg(SEC[tf]); cs = cisd_lux(bb['open'], bb['close']); nz = np.flatnonzero(cs)
    HTFC[tf] = ((bb['time'][nz] + SEC[tf]).astype(np.int64), cs[nz])
def htf_dir(tf, t):
    ct, dv = HTFC[tf]; q = int(np.searchsorted(ct, t, side='right')) - 1
    return int(dv[q]) if q >= 0 else 0
d1t, ZZ = session_zones(m1)
M5x = agg(300); M5ct = M5x['time'] + 300; ses5 = np.searchsorted(d1t, M5x['time'], side='right') - 1
up_t = np.full(len(d1t), np.inf); dn_t = np.full(len(d1t), np.inf)
for k in range(len(M5ct)):
    q = ses5[k]
    if q < 0 or np.isnan(ZZ[q, 1]): continue
    if M5x['close'][k] > ZZ[q, 1] and M5ct[k] < up_t[q]: up_t[q] = M5ct[k]
    if M5x['close'][k] < ZZ[q, 3] and M5ct[k] < dn_t[q]: dn_t[q] = M5ct[k]
def dz_feats(d, e, t):
    q = int(np.searchsorted(d1t, t, side='right')) - 1
    if q < 0 or np.isnan(ZZ[q, 0]): return 9, 0
    Z1, Z2, Z3, Z4 = ZZ[q]
    loc = 2 if e > Z2 else 1 if e > Z1 else 0 if e >= Z3 else -1 if e >= Z4 else -2
    brk = (1 if up_t[q] <= t else 0) - (1 if dn_t[q] <= t else 0)
    return loc * d, brk * d

# ---------- management (tick path): plain / breakeven at x R / 50% partial at 1R + breakeven ----------
def manage(e, d, entry, risk, hz, alg=None):
    px = BID if d > 0 else ASK
    reach = lambda lvl, a: first_true((lambda x, y: px[x:y] >= lvl) if d > 0 else (lambda x, y: px[x:y] <= lvl), a, hz)
    back = lambda lvl, a: first_true((lambda x, y: px[x:y] <= lvl) if d > 0 else (lambda x, y: px[x:y] >= lvl), a, hz)
    sl = entry - d * risk; s0 = back(sl, e); s0 = s0 if s0 >= 0 else hz
    lv = {L: reach(entry + d * L * risk, e) for L in (1, 1.5, 2, 3)}
    lv = {L: (i if 0 <= i else hz) for L, i in lv.items()}
    lastR = ((px[min(hz, NT - 1)] - entry) * d) / risk; tsec = lambda i: int(TT[min(i, NT - 1)] // 1000)
    c = P['Comm'] / risk; out = {}
    for T in (2, 3):
        iT = lv[T]
        if iT < s0: out[f'T{T}'] = (T - c, tsec(iT))
        elif s0 < hz: out[f'T{T}'] = (-1 - c, tsec(s0))
        else: out[f'T{T}'] = (lastR - c, tsec(hz))
        for x in (1, 1.5):
            ix = lv[x]
            if ix >= s0: out[f'T{T}_BE{x}'] = out[f'T{T}']; continue
            b = back(entry, ix); b = b if b >= 0 else hz
            if iT < b: r = (T - c, tsec(iT))
            elif b < hz: r = (-c, tsec(b))
            else: r = (lastR - c, tsec(hz))
            out[f'T{T}_BE{x}'] = r
            if x == 1:   # half off at 1R, rest with breakeven stop to T
                out[f'T{T}_P1'] = (0.5 * 1 + 0.5 * (r[0] + c) - c, r[1])
        if f'T{T}_P1' not in out: out[f'T{T}_P1'] = out[f'T{T}']
    # TA2: target = aligning level when it lies beyond 2R, else 2R
    if alg is not None and alg > 2 * risk:
        iA = reach(entry + d * alg, e); iA = iA if iA >= 0 else hz
        out['TA2'] = (alg / risk - c, tsec(iA)) if iA < s0 else ((-1 - c, tsec(s0)) if s0 < hz else (lastR - c, tsec(hz)))
    else: out['TA2'] = out['T2']
    # T2_RUN: half off at 2R, rest trails at max(entry + 1R, best - 1.5R)
    i2 = lv[2]; out['T2_RUN'] = out['T2']
    if i2 < s0 and i2 < hz:
        seg = px[i2:hz]
        ext = np.maximum.accumulate(seg) if d > 0 else np.minimum.accumulate(seg)
        trl = np.maximum(entry + risk, ext - 1.5 * risk) if d > 0 else np.minimum(entry - risk, ext + 1.5 * risk)
        hit = np.flatnonzero(seg <= trl) if d > 0 else np.flatnonzero(seg >= trl)
        if len(hit): j = i2 + int(hit[0]); rr = (px[j] - entry) * d / risk
        else: j = hz; rr = lastR
        out['T2_RUN'] = (0.5 * 2 + 0.5 * rr - c, tsec(j))
    # T2_TS4: 2R, but close at market after 4 h if +1R was never reached (and SL not hit)
    tcut = int(np.searchsorted(TT, TT[e] + 4 * 3600 * 1000)); out['T2_TS4'] = out['T2']
    if tcut < hz and lv[1] >= tcut and s0 >= tcut:
        out['T2_TS4'] = ((px[tcut] - entry) * d / risk - c, tsec(tcut))
    return out

# ---------- signals ----------
trades = []; cnt = {}
HZ = P['Horizon'] * 86400 * 1000
ZALL = {tf: zones_for(tf) for tf in ('H4', 'H2', 'H1', 'M30', 'M15')}
for otf in P['OBTFs'].split(','):
    Z = ZALL[otf] if otf in ZALL else zones_for(otf); c = dict(obs=len(Z), retest=0, inval=0, nocisd=0)
    for q in Z:
        if q['formed'] < START - 86400 or q['formed'] >= END: continue
        d = q['d']; j0 = int(np.searchsorted(MT, q['formed']))
        j = first_true((lambda a, b: ML[a:b] <= q['top']) if d > 0 else (lambda a, b: MH[a:b] >= q['btm']), j0, len(MT))
        if j < 0: continue
        rt = int(MT[j])                       # retest minute
        if q['mit'] <= rt: continue           # already invalidated (gap through) -> not a retest
        if rt < START or rt >= END: continue
        c['retest'] += 1; any_trade = False
        ovl = sum(1 for o2, Zs in ZALL.items() if o2 != otf and any(z2['d'] == d and z2['formed'] <= rt < z2['mit'] and
                  z2['btm'] <= q['top'] and z2['top'] >= q['btm'] for z2 in Zs))
        qa = int(np.searchsorted(SNT, rt, side='right')) - 1; lvls = SNS[qa] if d > 0 else SNR[qa]
        lvlz = min([0.0 if q['btm'] <= v <= q['top'] else min(abs(v - q['top']), abs(v - q['btm'])) for v in lvls], default=999.0)
        for (ltf, nm), ev in CI.items():
          arr = ev[d]; ks = int(np.searchsorted(arr, rt, side='right')); seq = 0
          while ks < len(arr) and seq < P['MaxSeq']:
            ce = int(arr[ks]); ks += 1
            if ce > rt + 60 + (P['WinMin'] if seq == 0 else P['ReWin']) * 60: break   # no (more) CISD in window
            if q['mit'] <= ce: break           # OB invalidated before confirmation
            e = int(np.searchsorted(TT, ce * 1000))
            if e >= NT: break
            entry = ASK[e] if d > 0 else BID[e]
            obsl = q['btm'] - P['SLBuf'] if d > 0 else q['top'] + P['SLBuf']
            raw = (entry - obsl) * d
            if raw <= 0.3: continue            # price already through the SL side
            any_trade = True; seq += 1
            hz = int(np.searchsorted(TT, TT[e] + HZ))
            rec = dict(otf=otf, ltf=ltf, ct=nm, d=d, t=ce, rt=rt, formed=q['formed'], entry=float(entry), raw=float(raw),
                       obsz=float(q['top'] - q['btm']), delay=(ce - rt) / 60.0, seq=seq - 1,
                       obid=f"{otf}|{q['start']}|{d}", ovl=ovl, lvlz=float(lvlz))
            tpa = align_tp(d, entry, ce); rec['alg'] = None if tpa is None else float((tpa - entry) * d)
            rec['h4'] = htf_dir('H4', ce) * d; rec['h1'] = htf_dir('H1', ce) * d; rec['m15'] = htf_dir('M15', ce) * d
            rec['loc'], rec['brk'] = dz_feats(d, entry, ce)
            if P['Mgmt']:
                for cap in (20, 30): rec[f'm{cap}'] = manage(e, d, entry, min(raw, cap), hz, rec['alg'])
            for cap in [int(x) for x in P['Caps'].split(',')]:
                risk = min(raw, cap); sl = entry - d * risk
                s = first_true((lambda a, b: BID[a:b] <= sl) if d > 0 else (lambda a, b: ASK[a:b] >= sl), e, hz)
                endi = s if s >= 0 else hz
                seg = BID[e:endi + 1] if d > 0 else ASK[e:endi + 1]
                mfe = ((seg.max() - entry) if d > 0 else (entry - seg.min())) if len(seg) else 0.0
                last = (BID[min(hz, NT - 1)] - entry) * d if d > 0 else (entry - ASK[min(hz, NT - 1)])
                rec[f'c{cap}'] = dict(risk=float(risk), slhit=bool(s >= 0), mfe=float(mfe), last=float(last),
                                      slt=int(TT[s] // 1000) if s >= 0 else None)
            trades.append(rec)
        if not any_trade:
            # classify why: invalidated before any CISD vs no CISD
            if q['mit'] <= rt + 60 + P['WinMin'] * 60: c['inval'] += 1
            else: c['nocisd'] += 1
    cnt[otf] = c
    print(otf, c, 'signals so far', len(trades), flush=True)

# ---------- outcome per target ----------
def outcome(r, cap, tgt):
    x = r[f'c{cap}']; risk = x['risk']
    if tgt == 'align':
        if r['alg'] is None: return None
        dist = r['alg']
    else: dist = float(tgt) * risk
    if x['mfe'] >= dist: return (dist - P['Comm']) / risk
    if x['slhit']: return (-risk - P['Comm']) / risk
    return (x['last'] - P['Comm']) / risk
SPL = ts('2025-11-01')
def table(sel, lab):
    out = []
    for cap in [int(x) for x in P['Caps'].split(',')]:
        for tgt in ('1', '2', '3', 'align'):
            R = [(r['t'], outcome(r, cap, tgt)) for r in sel]; R = [(t, v) for t, v in R if v is not None]
            if not R: continue
            v = np.array([x[1] for x in R]); a = [x[1] for x in R if x[0] < SPL]; b = [x[1] for x in R if x[0] >= SPL]
            out.append(f"cap{cap} {tgt:>5}: n {len(v):5d} win {np.mean(v > 0)*100:4.0f}% R/tr {v.mean():+.3f} netR {v.sum():+7.1f} "
                       f"| Y1 {np.mean(a) if a else 0:+.3f} Y2 {np.mean(b) if b else 0:+.3f}")
    print(f"== {lab}  ({len(sel)} signals)"); [print('   ' + o) for o in out]
print(f"\nOB retest -> LTF CISD | window {P['WinMin']}m | {P['Start']} -> {P['End']} | R per trade after commission")
table(trades, 'ALL')
for otf in P['OBTFs'].split(','):
    for ltf in P['LTFs'].split(','):
        for nm in ('lux', 'aa'):
            table([r for r in trades if r['otf'] == otf and r['ltf'] == ltf and r['ct'] == nm], f"{otf} OB | {ltf} {nm}")
if P['TrOut']: json.dump(dict(trades=trades, cnt=cnt), open(P['TrOut'], 'w'))
