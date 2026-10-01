"""Order-block retest + M5 CISD sim (OB EA, before it is built).

Zones = mql5/OB_Detector_v1.06.mq5 exactly (volume pivot length 5, wick mitigation, per TF, closed bars).
Setup (bullish, sells mirrored):
  1. zone still untested: first touch after it formed = a 1-min bar whose low <= zone top  -> setup armed
  2. a LATER M5 candle (index > the touch candle) is a bullish CISD (AlgoAlpha port, Tol 0.7) that CLOSES
     STRICTLY ABOVE the zone top (a close inside the zone doesn't count, keep waiting)
  3. entry at the next tick (ask), SL = zone bottom - SLBuf, TP = entry + RR x risk   (one trade per zone)
  The setup dies if the detector mitigates the zone first (TF candle close) -- the EA wouldn't see it any more.
  Immediate=1: if the last CISD on CisdTF before the touch already points the zone's way, enter AT the touch
             (first tick reaching the edge) instead of waiting.
  MaxChase=X : the waited-for CISD closing more than X beyond the edge -> trade skipped (0 = off).
  Filter=H1  : only trade when the last closed CISD on that TF points the trade's way ('' = off).
  CisdType=aa|lux : AlgoAlpha port (Tol 0.7, default -- what V6S-ICT uses) or LuxAlgo "CISD" Classic mode
             (no noise filter, only the latest level, level expires after LuxMaxLen bars). Used for CisdTF and Filter.
  NoEntry=HH:MM-HH:MM : no new entries in this IST window (e.g. 12:30-17:30 = London), '' = off.
  Bias=f1.json,f2.json : V6S-ICT direction -- trade only the way of the latest V6S-ICT v2.15 entry (REV / BO / TRAP,
             from s_v6.py TrOut files, which need the 'dir' field), held until an opposite entry; none yet = no trade.
  DZF=1      : V6S-ICT DZ filter -- an M5 close above the session's upper outer zone line (Z2) blocks bearish
             OB trades for the rest of that gap-session, a close below Z4 blocks bullish ones.
  ImmOnly=1  : immediate entries only (no waiting for a later CISD).
Every setup is an independent 1-lot trade (overlaps allowed); each RR in RRs is a separate book.

    python s_ob.py [TFs=M5,M15,M30] [RRs=1,2] [Start=2024-10-01] [End=2026-09-25] [MaxSL=0] [TrOut=file.json]
Ticks: synthetic from 1-min bars before 2026-04-01, real ticks after (TickSrc=mixed), like the other sims."""
import sys, json, numpy as np, datetime as dt
from ohlc import synth_ticks

P = dict(TFs='M5,M15,M30', RRs='1,2', Len=5, SLBuf=0.5, Tol=0.7, Lot=1.0, Contract=100.0, MinSpread=0.26, Comm=0.0,
         SwapLongPerLot=-55.04, MaxSL=0.0, Start='2024-10-01', End='2026-09-25', TickSrc='mixed', Bal=77581.0,
         Data='m1_2y_tv.npy', TrOut='', CisdTF='M5', Immediate=0, MaxChase=0.0, Filter='', ImmOnly=0, CisdType='aa', LuxMaxLen=100, NoEntry='', Bias='', DZF=0)
for a in sys.argv[1:]:
    k, v = a.split('='); P[k] = type(P[k])(v) if not isinstance(P[k], str) else v
SEC = dict(M1=60, M3=180, M5=300, M15=900, M30=1800, H1=3600, H4=14400)
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

# ---------------- zones: OB_Detector_v1.06 per TF ----------------
def zones_for(tf):
    sec = SEC[tf]; b = agg(sec); T, H, L, V = b['time'], b['high'], b['low'], b['tv']; n = len(T); N = P['Len']
    os_ = 0; act = {1: [], -1: []}; out = []
    for i in range(N, n):
        c = i - N; up = H[i - N + 1:i + 1].max(); lo = L[i - N + 1:i + 1].min()
        if H[c] > up: os_ = 0
        elif L[c] < lo: os_ = 1
        if c >= N and all(V[c - j] < V[c] for j in range(1, N + 1)) and all(V[c + j] <= V[c] for j in range(1, N + 1)):
            hl2 = (H[c] + L[c]) / 2
            z = dict(tf=tf, dir=1 if os_ == 1 else -1, top=hl2 if os_ == 1 else H[c], btm=L[c] if os_ == 1 else hl2,
                     ob=int(T[c]), formed=int(T[i]) + sec, mit=None)
            act[z['dir']].insert(0, z); out.append(z)
        close_t = int(T[i]) + sec
        for z in [z for z in act[1] if lo < z['btm']]: z['mit'] = close_t
        for z in [z for z in act[-1] if up > z['top']]: z['mit'] = close_t
        act[1] = [z for z in act[1] if z['mit'] is None]; act[-1] = [z for z in act[-1] if z['mit'] is None]
    return out

# ---------------- confirmation-TF CISD (same port as s_v6.py CisdTF); CisdTF = M1 / M3 / M5 ----------------
CSEC = SEC[P['CisdTF']]
M5 = agg(CSEC); M5T, M5O, M5C = M5['time'], M5['open'], M5['close']
def cisd_lux(O, C):
    """LuxAlgo 'Change in State of Delivery' Classic mode, confirmed bars: +1 bullish / -1 bearish CISD."""
    cs = np.zeros(len(C), dtype=np.int8); ob = oe = None; mx = P['LuxMaxLen']
    for n in range(1, len(C)):
        bull, bear = C[n] > O[n], C[n] < O[n]; pbull, pbear = C[n-1] > O[n-1], C[n-1] < O[n-1]
        if bull and pbear: ob = (n, O[n])       # bull after bear -> level for a bearish CISD
        if bear and pbull: oe = (n, O[n])       # bear after bull -> level for a bullish CISD
        if ob is not None:
            if n - ob[0] <= mx:
                if C[n] < ob[1]: cs[n] = -1; ob = None
            else: ob = None
        if oe is not None:
            if n - oe[0] <= mx:
                if C[n] > oe[1]: cs[n] = 1; oe = None
            else: oe = None
    return cs

def cisd_series(O, C):
    if P['CisdType'] == 'lux': return cisd_lux(O, C)
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
CS = cisd_series(M5O, M5C)
if P['Filter']:
    FB = agg(SEC[P['Filter']]); FCS = cisd_series(FB['open'], FB['close'])
    FNZ = np.flatnonzero(FCS); FCL = FB['time'][FNZ] + SEC[P['Filter']]
def htf_ok(d, t):
    if not P['Filter']: return True
    q = int(np.searchsorted(FCL, t, side='right')) - 1
    return q >= 0 and FCS[FNZ[q]] == d
filtered = 0
M5CLOSE = M5T + CSEC
NZ = np.flatnonzero(CS)   # candles with a CISD

def first_true(arr_fn, a, n):
    """first index >= a where arr_fn(slice) is True, scanning in growing chunks; -1 if none"""
    step = 2000
    while a < n:
        b = min(n, a + step); hit = np.flatnonzero(arr_fn(a, b))
        if len(hit): return a + int(hit[0])
        a = b; step *= 4
    return -1

# ---------------- setups ----------------
setups = []; chased = 0
for tf in P['TFs'].split(','):
    for z in zones_for(tf):
        d = z['dir']; a = int(np.searchsorted(MT, z['formed']))
        mit = z['mit'] if z['mit'] is not None else 1 << 62
        # 1. first touch after formation (1-min bars), must happen before the zone is mitigated
        k = first_true((lambda s, e: ML[s:e] <= z['top']) if d > 0 else (lambda s, e: MH[s:e] >= z['btm']), a, len(MT))
        if k < 0 or MT[k] >= mit: continue
        touch_t = int(MT[k]); tb = int(np.searchsorted(M5T, (touch_t // CSEC) * CSEC))   # touch candle on the CISD TF
        # 2. later M5 CISD closing strictly beyond the zone edge, before mitigation
        edge = z['top'] if d > 0 else z['btm']
        sl = (z['btm'] - P['SLBuf']) if d > 0 else (z['top'] + P['SLBuf'])
        if P['Immediate']:
            q = int(np.searchsorted(M5CLOSE[NZ], touch_t, side='right')) - 1   # last CISD closed by the touch minute
            if q >= 0 and CS[NZ[q]] == d:
                if not htf_ok(d, touch_t): filtered += 1; continue
                if START <= touch_t < END: setups.append(dict(z, touch=touch_t, entry_t=touch_t, sl=sl, how='IMM', edge=edge))
                continue
        if P['ImmOnly']: continue
        j = first_true((lambda s, e: (CS[s:e] == d) & ((M5C[s:e] > edge) if d > 0 else (M5C[s:e] < edge))), tb + 1, len(M5T))
        if j < 0 or M5CLOSE[j] >= mit: continue
        et = int(M5CLOSE[j])
        if not (START <= et < END): continue
        if P['MaxChase'] > 0 and abs(M5C[j] - edge) > P['MaxChase']: chased += 1; continue
        if not htf_ok(d, et): filtered += 1; continue
        setups.append(dict(z, touch=touch_t, entry_t=et, sl=sl, how='CISD', edge=edge))
setups.sort(key=lambda s: s['entry_t'])

# ---------------- ticks + exits ----------------
if P['TickSrc'] == 'ohlc': TK = synth_ticks(m1[(MT >= START - 86400) & (MT < END + 40 * 86400)])
else:
    syn = synth_ticks(m1[(MT >= START - 86400) & (MT < REAL_FROM)]); real = np.load('ticks.npy')
    TK = np.concatenate([syn, real[real[:, 0] >= REAL_FROM * 1000]])
TMS, BID = TK[:, 0], TK[:, 1]; ASK = np.maximum(TK[:, 2], BID + P['MinSpread']); NT = len(TMS)

def swap_nights(t0, t1):
    d0, d1 = int(t0 // 86400), int(t1 // 86400); return sum(3 if (d + 3) % 7 == 2 else 1 for d in range(d0, d1))

BIAS_T = BIAS_D = None
if P['Bias']:
    ev = sorted((x['t'], x['dir']) for f in P['Bias'].split(',') for x in json.load(open(f)) if x['typ'] in ('REV', 'BO', 'TRAP'))
    BIAS_T = np.array([e[0] for e in ev]); BIAS_D = np.array([e[1] for e in ev])
if P['DZF']:
    from sessz import session_zones
    d1t, ZZ = session_zones(m1); Z2, Z4 = ZZ[:, 1], ZZ[:, 3]
    B5 = agg(300); b5c = B5['time'] + 300; ses = np.searchsorted(d1t, B5['time'], side='right') - 1
    up_t = np.full(len(d1t), np.inf); dn_t = np.full(len(d1t), np.inf)
    for k in range(len(b5c)):
        q = ses[k]
        if q < 0 or np.isnan(Z2[q]): continue
        if B5['close'][k] > Z2[q] and b5c[k] < up_t[q]: up_t[q] = b5c[k]
        if B5['close'][k] < Z4[q] and b5c[k] < dn_t[q]: dn_t[q] = b5c[k]
def ctx_ok(d, t):
    if BIAS_T is not None:
        q = int(np.searchsorted(BIAS_T, t, side='right')) - 1
        if q < 0 or BIAS_D[q] != d: return False
    if P['DZF']:
        q = int(np.searchsorted(d1t, t, side='right')) - 1
        if q >= 0 and ((d < 0 and up_t[q] <= t) or (d > 0 and dn_t[q] <= t)): return False
    return True
nctx = sum(not ctx_ok(s['dir'], s['entry_t']) for s in setups)
setups = [s for s in setups if ctx_ok(s['dir'], s['entry_t'])]

def ist_min(hm): h, m = hm.split(':'); return int(h) * 60 + int(m)
NOWIN = tuple(ist_min(x) for x in P['NoEntry'].split('-')) if P['NoEntry'] else None
def blocked(t):
    if NOWIN is None: return False
    m = int((t + 19800) % 86400) // 60; a, b = NOWIN
    return a <= m < b if a <= b else (m >= a or m < b)
nblocked = sum(blocked(s['entry_t']) for s in setups)
setups = [s for s in setups if not blocked(s['entry_t'])]
RRs = [float(x) for x in P['RRs'].split(',')]
books = {rr: [] for rr in RRs}; skipped_cap = 0
for s in setups:
    e = int(np.searchsorted(TMS, s['entry_t'] * 1000.0))
    if e >= NT: continue
    if s['how'] == 'IMM':   # the tick inside the touch minute that first reaches the edge
        e2 = int(np.searchsorted(TMS, s['entry_t'] * 1000.0 + 60000))
        hit = np.flatnonzero(BID[e:e2] <= s['edge']) if s['dir'] > 0 else np.flatnonzero(BID[e:e2] >= s['edge'])
        if len(hit): e += int(hit[0])
    d = s['dir']; entry = ASK[e] if d > 0 else BID[e]; risk = (entry - s['sl']) * d
    if risk <= 0: continue
    if P['MaxSL'] > 0 and risk > P['MaxSL']: skipped_cap += 1; continue
    for rr in RRs:
        tp = entry + d * rr * risk
        if d > 0: k = first_true(lambda a, b: (BID[a:b] <= s['sl']) | (BID[a:b] >= tp), e, NT)
        else: k = first_true(lambda a, b: (ASK[a:b] >= s['sl']) | (ASK[a:b] <= tp), e, NT)
        if k < 0: continue
        px = BID[k] if d > 0 else ASK[k]; win = (px >= tp) if d > 0 else (px <= tp)
        xt = TMS[k] / 1000.0
        pl = (px - entry) * d * P['Contract'] * P['Lot'] - 2 * P['Comm'] * P['Lot']
        if d > 0: pl += P['SwapLongPerLot'] * P['Lot'] * swap_nights(s['entry_t'], xt)
        books[rr].append(dict(tf=s['tf'], dir=d, entry_t=s['entry_t'], exit_t=xt, entry=round(float(entry), 3),
                              sl=round(float(s['sl']), 3), tp=round(float(tp), 3), risk=round(float(risk), 3),
                              win=bool(win), pl=round(float(pl), 2), how=s['how'], zone=[round(float(s['btm']), 3), round(float(s['top']), 3)],
                              ob=ist(s['ob']), formed=ist(s['formed']), touch=ist(s['touch'])))

# ---------------- report ----------------
def stats(tr):
    if not tr: return None
    tr = sorted(tr, key=lambda x: x['exit_t']); eq = P['Bal']; peak = eq; dd = 0.0; mon = {}
    for x in tr:
        eq += x['pl']; peak = max(peak, eq); dd = max(dd, (peak - eq) / peak * 100)
        m = dt.datetime.fromtimestamp(int(x['exit_t']), dt.timezone.utc).strftime('%Y-%m'); mon[m] = mon.get(m, 0) + x['pl']
    w = sum(x['win'] for x in tr)
    return dict(n=len(tr), win=w / len(tr) * 100, net=sum(x['pl'] for x in tr), dd=dd, lose=[m for m, v in mon.items() if v < 0],
                mon=mon, risk=np.median([x['risk'] for x in tr]), rmax=max(x['risk'] for x in tr))

print(f"OB retest + {P['CisdTF']} CISD ({P['CisdType']}) | {P['Start']} -> {P['End']} | ticks {P['TickSrc']} | lot {P['Lot']} | SL buf {P['SLBuf']}"
      f"{' | SL cap ' + str(P['MaxSL']) + ' (skip), skipped ' + str(skipped_cap) if P['MaxSL'] > 0 else ''} | setups {len(setups)}"
      f"{' | immediate entry on' if P['Immediate'] else ''}{' | chase > ' + str(P['MaxChase']) + ' skipped ' + str(chased) if P['MaxChase'] > 0 else ''}"
      f"{' | ' + P['Filter'] + ' CISD filter, blocked ' + str(filtered) if P['Filter'] else ''}{' | immediate only' if P['ImmOnly'] else ''}"
      f"{' | no entries ' + P['NoEntry'] + ' IST, blocked ' + str(nblocked) if NOWIN else ''}"
      f"{' | V6S bias' if P['Bias'] else ''}{' + DZ filter' if P['DZF'] else ''}{', blocked ' + str(nctx) if P['Bias'] or P['DZF'] else ''}")
for rr in RRs:
    print(f"\n=== TP 1:{rr:g} ===")
    for lab in P['TFs'].split(',') + ['ALL', 'IMM', 'CISD']:
        st = stats([x for x in books[rr] if lab == 'ALL' or x['tf'] == lab or x['how'] == lab])
        if not st: print(f"  {lab:4s} no trades"); continue
        print(f"  {lab:4s} trades {st['n']:4d}  win {st['win']:5.1f}%  net {st['net']:+11,.0f}  drop {st['dd']:5.1f}%  "
              f"losing months {len(st['lose']):2d}/{len(st['mon'])}  SL median {st['risk']:.2f} max {st['rmax']:.2f}")
    st = stats(books[rr])
    if st: print('  months: ' + ' '.join(f"{m[2:]} {v:+,.0f}" for m, v in st['mon'].items()))
if P['TrOut']:
    json.dump({f"{rr:g}": books[rr] for rr in RRs}, open(P['TrOut'], 'w'), indent=0)
