"""Fresh Major -> MSS -> OB pullback sim (new method, 2026-10-03).

BUY (sell mirrored), on the structure timeframe TF (M5 or M3):
  1. a FRESH Major support is set on TF (V6S Major/Minor port, pivot 5)
  2. MSS: a later TF candle CLOSES above the most recent swing high at that moment (the newer of TF's Major / Minor
     resistance)
  3. after the MSS: the nearest active bullish OB on M3 or M5 (OB_Detector_v1.07 rules, 3 newest active per side)
     below price whose bottom is at/above the Major low; if none yet, wait for one to form
  4. ENTRY when price pulls back to the OB top (first tick at/below it), SL = OB bottom - SLBuf (0.5)
  5. TP: Tgt=align -> nearest aligning RESISTANCE above entry - 1.0 (3.x rule: >= 3 TFs H4-M5, Major or Minor, M5 Majors
     only, no M3; none -> FallbackRR); Tgt=1/2/3 -> RR x risk
  Cancelled by a TF close below the Major low, a newer Major support, or ValidBars TF candles without entry.
  One position at a time. Real Exness Real7 ticks (build_real7.py). P/L in points (= $ per 0.01 lot), Comm per trade.

  Filters (2026-10-03 deep check): MinSwing = MSS level must be >= this many points from the Major; MinWait = entry no
  sooner than this many minutes after the MSS; NoHours = IST entry hours to skip, e.g. 3-7; MinRisk / MaxRisk =
  skip the entry if the SL distance is outside this band (0 = off).
  Aligning / DZ options (2026-10-03): RoomTP=1 skip if an aligning level of the opposite type lies between entry and
  TP; MajAlign=X only if the Major is within X of an aligning level of its type (buy: support); Hybrid=1 TP = the
  nearest opposite aligning level -/+ TPBuf when it lies between 1R and 2R, else Tgt x risk; DZF=1 V6S DZ filter (M5
  close above Z2 -> no sells / below Z4 -> no buys for the rest of the gap-session); DZLoc=1 buys only if the Major
  low is at/below the top of the session's support zone (Z3-Z4) + ZoneBuf, sells mirrored.
  MaxPos = max positions open at once (1 = one at a time, 0 = unlimited); PerDir=1 -> at most one per direction.
    python s_mss.py TF=M5 Tgt=align|1|2|3 [Start=2024-11-01] [End=2026-10-02]"""
import sys, json, numpy as np, datetime as dt
from v6sim import MajorMinor

P = dict(TF='M5', Tgt='2', Start='2024-11-01', End='2026-10-02', SLBuf=0.5, TPBuf=1.0, FallbackRR=2.0, ValidBars=48,
         OBTFs='M3,M5', Len=5, Comm=0.0, AlignTFs='H4,H2,H1,M30,M15,M10,M5', AlignMin=3, TrOut='', MinSwing=0.0, MinWait=0.0, NoHours='', MinRisk=0.0, MaxRisk=0.0, RoomTP=0, MajAlign=0.0, Hybrid=0, DZF=0, DZLoc=0, MaxPos=1, PerDir=0, StructTFs='', KeepOld=0, MaxSetups=5, ReEntry=0)
for a in sys.argv[1:]:
    k, v = a.split('='); P[k] = type(P[k])(v) if not isinstance(P[k], str) else v
SEC = dict(M1=60, M3=180, M5=300, M10=600, M15=900, M30=1800, H1=3600, H2=7200, H4=14400)
ts = lambda s: int(np.datetime64(s).astype('datetime64[s]').astype(np.int64))
START, END = ts(P['Start']), ts(P['End'])

m1 = np.load('m1_real7_tv.npy'); MT, MH, ML = m1['time'], m1['high'], m1['low']
def agg(sec):
    b = (MT // sec) * sec; idx = np.flatnonzero(np.diff(b)) + 1
    st = np.concatenate([[0], idx]); en = np.concatenate([idx, [len(b)]])
    return dict(time=b[st], open=m1['open'][st], close=m1['close'][en - 1], high=np.maximum.reduceat(MH, st),
                low=np.minimum.reduceat(ML, st), tv=np.add.reduceat(m1['tv'], st))

# ---------- OBs on M3 / M5 ----------
def zones_for(tf):
    sec = SEC[tf]; b = agg(sec); T, H, L, V = b['time'], b['high'], b['low'], b['tv']; n = len(T); N = P['Len']
    os_ = 0; act = {0: [], 1: []}; out = []
    for i in range(N, n):
        c = i - N; up = H[i - N + 1:i + 1].max(); lo = L[i - N + 1:i + 1].min()
        if H[c] > up: os_ = 0
        elif L[c] < lo: os_ = 1
        if c >= N and all(V[c - j] < V[c] for j in range(1, N + 1)) and all(V[c + j] <= V[c] for j in range(1, N + 1)):
            hl2 = (H[c] + L[c]) / 2; side = 0 if os_ == 1 else 1
            z = dict(tf=tf, side=side, top=hl2 if side == 0 else H[c], btm=L[c] if side == 0 else hl2,
                     id=(tf, int(T[c])), formed=int(T[i]) + sec, mit=1 << 62)
            act[side].insert(0, z); out.append(z)
        ct = int(T[i]) + sec
        for z in act[0]:
            if lo < z['btm']: z['mit'] = ct
        for z in act[1]:
            if up > z['top']: z['mit'] = ct
        act[0] = [z for z in act[0] if z['mit'] == 1 << 62]; act[1] = [z for z in act[1] if z['mit'] == 1 << 62]
    return out
ALLZ = []
for tf in P['OBTFs'].split(','): ALLZ += zones_for(tf)
ALLZ.sort(key=lambda z: z['formed'])

# ---------- aligning levels over time (TP) ----------
SNT, SNS, SNR = [0], [()], [()]
if True:   # aligning snapshots (TP and analysis features)
    ev = []; MMB = {}
    for tf in P['AlignTFs'].split(','):
        b = agg(SEC[tf]); MMB[tf] = (b, MajorMinor(tf))
        ev += [(int(b['time'][k]) + SEC[tf], tf, k) for k in range(len(b['time']))]
    ev.sort(); j = 0
    while j < len(ev):
        t0 = ev[j][0]
        while j < len(ev) and ev[j][0] == t0:
            _, tf, k = ev[j]; b, mm = MMB[tf]; mm.add(b['high'][k], b['low'][k], b['close'][k], int(b['time'][k])); j += 1
        cnt = {}
        for tf, (b, mm) in MMB.items():
            for sd, v, kind in mm.levels():
                if tf == 'M5' and kind == 'Min': continue
                cnt.setdefault((sd, v), set()).add(tf)
        sup = tuple(sorted(v for (sd, v), s in cnt.items() if sd == 'S' and len(s) >= P['AlignMin']))
        res = tuple(sorted(v for (sd, v), s in cnt.items() if sd == 'R' and len(s) >= P['AlignMin']))
        if sup != SNS[-1] or res != SNR[-1]: SNT.append(t0); SNS.append(sup); SNR.append(res)

# ---------- features: HTF CISD state, previous-day range ----------
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
HTF = {}
for tf in ('M15', 'H1', 'H4'):
    b = agg(SEC[tf])
    for nm, fn in (('aa', cisd_aa), ('lux', cisd_lux)):
        cs = fn(b['open'], b['close']); nz = np.flatnonzero(cs)
        HTF[tf + nm] = ((b['time'][nz] + SEC[tf]).astype(np.int64), cs[nz])
def htf_dir(key, t):
    ct, dv = HTF[key]; q = int(np.searchsorted(ct, t, side='right')) - 1
    return int(dv[q]) if q >= 0 else 0
D1 = agg(86400); D1R = D1['high'] - D1['low']
def prev_range(t):
    q = int(np.searchsorted(D1['time'], t, side='right')) - 2
    return float(D1R[q]) if q >= 0 else 0.0

# ---------- Dynamic Zones (gap sessions, as V6S v2.21) ----------
from sessz import session_zones
d1t, ZZ = session_zones(m1)
M5x = agg(300); M5ct = M5x['time'] + 300; ses5 = np.searchsorted(d1t, M5x['time'], side='right') - 1
up_t = np.full(len(d1t), np.inf); dn_t = np.full(len(d1t), np.inf)
for k in range(len(M5ct)):
    q = ses5[k]
    if q < 0 or np.isnan(ZZ[q, 1]): continue
    if M5x['close'][k] > ZZ[q, 1] and M5ct[k] < up_t[q]: up_t[q] = M5ct[k]
    if M5x['close'][k] < ZZ[q, 3] and M5ct[k] < dn_t[q]: dn_t[q] = M5ct[k]
def dz_at(t):
    q = int(np.searchsorted(d1t, t, side='right')) - 1
    return (q, ZZ[q]) if q >= 0 and not np.isnan(ZZ[q, 0]) else (q, None)

# ---------- structure TF ----------
TFB = agg(SEC[P['TF']]); TFC = TFB['time'] + SEC[P['TF']]; MMS = MajorMinor(P['TF'])

# ---------- ticks ----------
z = np.load('ticks_real7.npz'); TMS = z['t'].astype(np.float64); BID = z['b'] / 1000.0; ASK = BID + z['s'] / 1000.0; del z
NT = len(TMS)
def first_true(fn, a, n):
    step = 2000
    while a < n:
        b = min(n, a + step); hit = np.flatnonzero(fn(a, b))
        if len(hit): return a + int(hit[0])
        a = b; step *= 4
    return -1

NOH = set()
if P['NoHours']:
    a_, b_ = (int(x) for x in P['NoHours'].split('-')); NOH = set(range(a_, b_ + 1))
# ---------- loop ----------
# StructTFs: structure timeframes (default TF); KeepOld=1: a newer Major does not cancel older setups (max MaxSetups
# per TF and direction run in parallel); ReEntry=N: up to N more entries at the same OB after the previous trade closed
# (setup and OB still valid). Each OB trades at most 1 + ReEntry times.
STFS = (P['StructTFs'] or P['TF']).split(',')
STR = {}
for stf in STFS:
    b_ = agg(SEC[stf]); STR[stf] = dict(B=b_, C=b_['time'] + SEC[stf], MM=MajorMinor(stf), ti=0, prevX={1: -1, -1: -1})
active = {}; zi = 0; nextmit = 1 << 62; si = 0
SET = []
busy = 0; trades = []; openpos = []; MAXPOS = P['MaxPos']; PERDIR = P['PerDir']; obcount = {}
stats_c = dict(majors=0, mss=0, obs=0, fallback=0, reentries=0)
def obs_now(side, now):
    return [z for (tf, sd), lst in active.items() if sd == side for z in lst[:3]]
i0 = int(np.searchsorted(MT, START - 5 * 86400))
for i in range(i0, len(MT)):
    bt = int(MT[i]); now = bt + 60
    if now >= END: break
    while zi < len(ALLZ) and ALLZ[zi]['formed'] <= now:
        zz = ALLZ[zi]; active.setdefault((zz['tf'], zz['side']), []).insert(0, zz); zi += 1; nextmit = min(nextmit, zz['mit'])
    if nextmit <= now:
        nextmit = 1 << 62
        for key in list(active):
            active[key] = [q for q in active[key] if q['mit'] > now]
            for q in active[key]: nextmit = min(nextmit, q['mit'])
    while si + 1 < len(SNT) and SNT[si + 1] <= now: si += 1

    # A. pullback entries during this M1 candle
    for S in list(SET):
        if S not in SET or S['stage'] != 'pb' or bt < S['t0'] or bt < S.get('waitUntil', 0): continue
        d = S['d']; ob = S['ob']
        if ob['mit'] <= bt or obcount.get(ob['id'], 0) >= 1 + P['ReEntry']: SET.remove(S); continue
        openp = [(xx, dd) for xx, dd in openpos if xx > bt]
        if PERDIR and any(dd == d for xx, dd in openp): continue
        if not PERDIR and MAXPOS > 0 and len(openp) >= MAXPOS: continue
        edge = ob['top'] if d > 0 else ob['btm']
        if not (ML[i] <= edge if d > 0 else MH[i] >= edge): continue
        a = int(np.searchsorted(TMS, bt * 1000.0)); b = int(np.searchsorted(TMS, now * 1000.0))
        if P['MinWait'] > 0 and bt < S.get('mssT', S['t0']) + P['MinWait'] * 60: continue
        hit = np.flatnonzero(BID[a:b] <= edge) if d > 0 else np.flatnonzero(BID[a:b] >= edge)
        if not len(hit): continue
        if NOH and int(((TMS[a + int(hit[0])] / 1000 + 19800) % 86400) // 3600) in NOH: continue
        e = a + int(hit[0]); entry = ASK[e] if d > 0 else BID[e]
        sl = ob['btm'] - P['SLBuf'] if d > 0 else ob['top'] + P['SLBuf']
        risk = (entry - sl) * d
        S_maj, S_mss, S_mssT, S_majT = S['maj'], S['mss'], S.get('mssT', S['t0']), S['majT']
        reentry = S.get('entries', 0) > 0
        SET.remove(S)
        if risk <= 0 or TMS[e] / 1000 < START: continue
        if (P['MinRisk'] > 0 and risk < P['MinRisk']) or (P['MaxRisk'] > 0 and risk > P['MaxRisk']): continue
        te_ = TMS[e] / 1000; lvR = SNR[si] if d > 0 else SNS[si]; lvS = SNS[si] if d > 0 else SNR[si]
        if P['MajAlign'] > 0 and not any(abs(S_maj - v) <= P['MajAlign'] for v in lvS): continue
        q, Z = dz_at(te_)
        if P['DZF'] and q >= 0 and ((d > 0 and dn_t[q] <= te_) or (d < 0 and up_t[q] <= te_)): continue
        if P['DZLoc'] and Z is not None:
            if d > 0 and S_maj > max(Z[2], Z[3]) + 2.5: continue
            if d < 0 and S_maj < min(Z[0], Z[1]) - 2.5: continue
        if P['Tgt'] == 'align':
            lv = [v for v in (SNR[si] if d > 0 else SNS[si]) if (v - d * P['TPBuf'] - entry) * d > 0]
            if lv: tp = (min(lv) if d > 0 else max(lv)) - d * P['TPBuf']
            else: tp = entry + d * P['FallbackRR'] * risk; stats_c['fallback'] += 1
        else:
            tp = entry + d * float(P['Tgt']) * risk
            if P['Hybrid']:
                cand = [v - d * P['TPBuf'] for v in lvR if (v - d * P['TPBuf'] - (entry + d * risk)) * d > 0 and (v - d * P['TPBuf'] - (entry + 2 * d * risk)) * d <= 0]
                if cand: tp = min(cand) if d > 0 else max(cand)
        if P['RoomTP'] and any((v - entry) * d > 0 and (tp - v) * d > 0 for v in lvR): continue
        k = first_true((lambda x, y: (BID[x:y] <= sl) | (BID[x:y] >= tp)) if d > 0 else (lambda x, y: (ASK[x:y] >= sl) | (ASK[x:y] <= tp)), e, NT)
        if k < 0: continue
        px = BID[k] if d > 0 else ASK[k]; busy = TMS[k] / 1000.0
        openpos[:] = [(xx, dd) for xx, dd in openpos if xx > bt] + [(busy, d)]
        obcount[ob['id']] = obcount.get(ob['id'], 0) + 1
        stats_c['reentries'] += reentry
        if P['ReEntry'] and obcount[ob['id']] < 1 + P['ReEntry']:
            S['entries'] = S.get('entries', 0) + 1; S['waitUntil'] = busy; SET.append(S)
        te = int(TMS[e] / 1000)
        lv0 = SNS[si] if d > 0 else SNR[si]
        alnd = min([abs(S_maj - v) for v in lv0], default=999.0)
        trades.append(dict(t=TMS[e] / 1000, x=busy, d=d, entry=float(entry), sl=float(sl), tp=float(tp), risk=float(risk),
                           r=float((tp - entry) * d / risk), pts=float((px - entry) * d - P['Comm']),
                           win=bool((px >= tp) if d > 0 else (px <= tp)), ob=f"{ob['tf']} {ob['btm']:.2f}-{ob['top']:.2f}",
                           obtf=ob['tf'], obsz=float(ob['top'] - ob['btm']), swing=float(abs(S_mss - S_maj)), wait=float(te - S_mssT) / 60,
                           setup=float(S_mssT - S_majT) / 60, hour=int(((te + 19800) % 86400) // 3600), wd=int(((te + 19800) // 86400 + 3) % 7),
                           alnd=float(alnd), prng=prev_range(te), spread=float(ASK[e] - BID[e]), stf=S['stf'], re=bool(reentry),
                           **{k: htf_dir(k, te) for k in HTF}))

    # B. structure candles closing now (every structure TF)
    for stf, R in STR.items():
        while R['ti'] < len(R['C']) and R['C'][R['ti']] <= now:
            k = R['ti']; R['ti'] += 1
            if R['C'][k] != now: continue
            MMS = R['MM']; B_ = R['B']
            MMS.add(B_['high'][k], B_['low'][k], B_['close'][k], int(B_['time'][k]))
            c = B_['close'][k]
            for d in (1, -1):
                X = MMS.MajSupX if d > 0 else MMS.MajResX; Y = MMS.MajSupY if d > 0 else MMS.MajResY
                if X >= 0 and X != R['prevX'][d]:
                    R['prevX'][d] = X
                    rx, ry = (MMS.MinResX, MMS.MinResY) if d > 0 else (MMS.MinSupX, MMS.MinSupY)
                    gx, gy = (MMS.MajResX, MMS.MajResY) if d > 0 else (MMS.MajSupX, MMS.MajSupY)
                    lvl = ry if rx >= gx else gy
                    if (lvl - Y) * d > max(0.0, P['MinSwing']) - (1e-9 if P['MinSwing'] <= 0 else 0) and (rx >= 0 or gx >= 0):
                        mine = [S for S in SET if S['stf'] == stf and S['d'] == d and S.get('entries', 0) == 0]
                        if not P['KeepOld']:
                            for S in mine: SET.remove(S)
                        elif len(mine) >= P['MaxSetups']: SET.remove(mine[0])
                        SET.append(dict(stf=stf, d=d, stage='mss', maj=Y, mss=lvl, bars=0, t0=now, majT=now)); stats_c['majors'] += 1
            for S in list(SET):
                if S['stf'] != stf or S['majT'] == now: continue
                d = S['d']
                S['bars'] += 1
                if (c < S['maj']) if d > 0 else (c > S['maj']): SET.remove(S); continue
                if S['bars'] > P['ValidBars']: SET.remove(S); continue
                if S['stage'] == 'mss' and (c > S['mss'] if d > 0 else c < S['mss']):
                    S['stage'] = 'ob'; S['t0'] = now; S['mssT'] = now; stats_c['mss'] += 1
    # C. OB selection for setups waiting for one
    for S in SET:
        if S['stage'] != 'ob': continue
        d = S['d']; side = 0 if d > 0 else 1; px = m1['close'][i]
        cand = [q for q in obs_now(side, now) if (q['top'] < px if d > 0 else q['btm'] > px)
                and (q['btm'] >= S['maj'] - 1e-9 if d > 0 else q['top'] <= S['maj'] + 1e-9)
                and obcount.get(q['id'], 0) < 1 + P['ReEntry']]
        if cand:
            S['ob'] = max(cand, key=lambda q: q['top']) if d > 0 else min(cand, key=lambda q: q['btm'])
            S['stage'] = 'pb'; S['t0'] = now; stats_c['obs'] += 1

# ---------- report ----------
def rep(tr, lab):
    if not tr: print(f"  {lab:8s} no trades"); return None
    eq = pk = dd = 0.0; mon = {}
    for x in sorted(tr, key=lambda x: x['x']):
        eq += x['pts']; pk = max(pk, eq); dd = max(dd, pk - eq)
        m = dt.datetime.fromtimestamp(int(x['x']), dt.timezone.utc).strftime('%y-%m'); mon[m] = mon.get(m, 0) + x['pts']
    print(f"  {lab:8s} trades {len(tr):4d}  win {sum(x['win'] for x in tr)/len(tr)*100:5.1f}%  net {eq:+8.1f} pts  drop {dd:6.1f}  "
          f"losing months {sum(v < 0 for v in mon.values()):2d}/{len(mon)}  avg risk {np.mean([x['risk'] for x in tr]):5.2f}  avg TP {np.mean([x['r'] for x in tr]):4.2f}R")
    return mon
SPL = ts('2025-11-01')
if trades:
    A_ = [x['pts'] / x['risk'] for x in trades if x['t'] < SPL]; B_ = [x['pts'] / x['risk'] for x in trades if x['t'] >= SPL]
    halves = f"R/trade Nov24-Oct25 {np.mean(A_) if A_ else 0:+.3f} (n{len(A_)})  Nov25-Oct26 {np.mean(B_) if B_ else 0:+.3f} (n{len(B_)})"
else: halves = ''
opts = ' '.join(f"{k}={P[k]}" for k in ('RoomTP', 'MajAlign', 'Hybrid', 'DZF', 'DZLoc', 'PerDir', 'StructTFs', 'KeepOld', 'ReEntry') if P[k]) + ('' if P['MaxPos'] == 1 else f" MaxPos={P['MaxPos']}")
print(f"MSS-OB [{opts or 'base'}]{' | swing>=' + str(P['MinSwing']) if P['MinSwing'] else ''}{' | wait>=' + str(P['MinWait']) + 'm' if P['MinWait'] else ''}{' | no ' + P['NoHours'] + ' IST' if P['NoHours'] else ''}{' | risk ' + str(P['MinRisk']) + '-' + str(P['MaxRisk']) if P['MinRisk'] or P['MaxRisk'] else ''} | structure {P['TF']} | OBs {P['OBTFs']} | TP {P['Tgt']} | {P['Start']} -> {P['End']} | majors {stats_c['majors']} "
      f"MSS {stats_c['mss']} OB-found {stats_c['obs']}" + (f" | TP fallback {stats_c['fallback']}" if P['Tgt'] == 'align' else ''))
mon = rep(trades, 'ALL'); print('  ' + halves); rep([x for x in trades if x['d'] > 0], 'BUY'); rep([x for x in trades if x['d'] < 0], 'SELL')
rep([x for x in trades if x['t'] >= ts('2026-01-01')], '2026')
if mon: print('  months: ' + ' '.join(f"{k} {v:+.0f}" for k, v in mon.items()))
if P['TrOut']: json.dump(trades, open(P['TrOut'], 'w'), default=float)
