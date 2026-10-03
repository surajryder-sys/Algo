"""algo_v2 (the Python order-block bot, XAUUSD) replayed on Real7 real ticks -- logic taken from algo_v2/*.py
(zone.py, candidates.py, entries.py, main.py run_once, sl_manager.py) and the OB/ATR bridge indicators.

  OBs: LuxAlgo volume-pivot OBs (length 5, wick mitigation; mitigated boxes deleted, newest 3 per side shown) on
       M1/M3/M5/M15. detected_price = mid price when the box appears; virgin = not touched since then.
  Effective direction: the newest of (M5 ATR-trail flip [ATR(2) x 2, closed bars], M5 newest bull OB start, M5 newest
       bear OB start). Same direction always eligible; opposite only if the OB started at/after that time (M5 only --
       M1 and M3 strict).
  Candidates each cycle, both directions:
    M1: newest two M1 OBs overall both this direction, newest virgin -> pending at edge +/- 0.25
    M3/M5: newest OB this direction, virgin; distance = detected price - edge: <0 none, <=4 MARKET,
           4..12 pending at edge + max(55% of distance, 4), else none
    SL = closest same-direction newest OB edge of M15/M5/M3 beyond the entry, -/+ 0.5. No TP.
  Winner = candidate whose entry is closest to price (market = 0). One position, one pending order.
  An eligible opposite winner closes the open position (square-off). Pending cancelled when its zone turns ineligible
  or its OB is deleted (zone blocked + per-TF cooldown floor). SL/TP close -> that direction blocked until a newer OB
  in that direction on M1/M3/M5. Each zone trades once.
  Trailing: best of (closest M15/M5 same-direction OB edge beyond price -/+ 0.5) and points (breakeven at +7,
  extreme - 10 once > +10).
  Cycle = every M1 bar open (live bot polls each second; OB/ATR data only change on bar closes). Fills, SL hits and
  point trailing are tick by tick. P/L in points (= $ at 0.01 lot), Comm per trade (default 0.07 = $7/lot).
    python s_algov2.py [Src=M1,M3,M5] [Start=2024-11-01] [End=2026-10-02] [Trail=1] [Square=1] [TrOut=file.json]"""
import sys, json, numpy as np, datetime as dt
from v6sim import MajorMinor
from sessz import session_zones

P = dict(Src='M1,M3,M5', Start='2024-11-01', End='2026-10-02', Comm=0.07, Len=5, Show=3, Trail=1, Square=1, TrOut='',
         TP='', TPBuf=1.0, Room=0.0, AlignAt=0.0, DZF=0, DZLoc=0, NoHours='', MinRisk=0.0, MaxRisk=0.0, MaxDay=0, HTF='', DZDeep=0,
         ATRAgree=0, Modes='M,P', Quiet=0, DirTF='M5', Skip='')
for a in sys.argv[1:]:
    k, v = a.split('='); P[k] = type(P[k])(v) if not isinstance(P[k], str) else v
SRC = set(P['Src'].split(','))
SEC = dict(M1=60, M3=180, M5=300, M15=900, M30=1800)
ts = lambda s: int(np.datetime64(s).astype('datetime64[s]').astype(np.int64))
START, END = ts(P['Start']), ts(P['End'])
U = dt.timezone.utc

m1 = np.load('m1_real7_tv.npy'); MT, MH, ML = m1['time'].astype(np.int64), m1['high'], m1['low']
def agg(sec):
    b = (MT // sec) * sec; idx = np.flatnonzero(np.diff(b)) + 1
    st = np.concatenate([[0], idx]); en = np.concatenate([idx, [len(b)]])
    return dict(time=b[st], open=m1['open'][st], close=m1['close'][en - 1], high=np.maximum.reduceat(MH, st),
                low=np.minimum.reduceat(ML, st), tv=np.add.reduceat(m1['tv'], st))

z = np.load('ticks_real7.npz'); TT = z['t']; BID = z['b'] / 1000.0; ASK = BID + z['s'] / 1000.0; del z
MID = (BID + ASK) / 2.0
TI = np.searchsorted(TT, MT * 1000); TI = np.append(TI, len(TT))   # ticks of minute i: TI[i]..TI[i+1]

def first_true(fn, a, n, step=64):
    while a < n:
        b = min(n, a + step); hit = np.flatnonzero(fn(a, b))
        if len(hit): return a + int(hit[0])
        a = b; step *= 4
    return -1

# ---------- OBs ----------
def zones_for(tf):
    sec = SEC[tf]; b = agg(sec); T, H, L, V = b['time'], b['high'], b['low'], b['tv']; n = len(T); N = P['Len']
    os_ = 0; act = {1: [], -1: []}; out = []
    for i in range(N, n):
        c = i - N; up = H[i - N + 1:i + 1].max(); lo = L[i - N + 1:i + 1].min()
        if H[c] > up: os_ = 0
        elif L[c] < lo: os_ = 1
        if c >= N and all(V[c - j] < V[c] for j in range(1, N + 1)) and all(V[c + j] <= V[c] for j in range(1, N + 1)):
            hl2 = (H[c] + L[c]) / 2; d = 1 if os_ == 1 else -1
            zz = dict(tf=tf, d=d, top=hl2 if d > 0 else H[c], btm=L[c] if d > 0 else hl2, start=int(T[c]),
                      formed=int(T[i]) + sec, mit=1 << 62)
            zz['key'] = f"{tf}|{d}|{zz['start']}"; act[d].insert(0, zz); out.append(zz)
        ct = int(T[i]) + sec
        for zz in act[1]:
            if lo < zz['btm']: zz['mit'] = ct
        for zz in act[-1]:
            if up > zz['top']: zz['mit'] = ct
        act[1] = [q for q in act[1] if q['mit'] == 1 << 62]; act[-1] = [q for q in act[-1] if q['mit'] == 1 << 62]
    return out
ALLZ = []
for tf in ('M1', 'M3', 'M5', 'M15', 'M30'): ALLZ += zones_for(tf)
ALLZ.sort(key=lambda q: q['formed'])
for q in ALLZ:   # detection price (mid at the moment the box appears) and first live touch (virgin until then)
    k = int(np.searchsorted(TT, q['formed'] * 1000)); q['det'] = float(MID[min(k, len(MID) - 1)])
    j0 = int(np.searchsorted(MT, q['formed']))
    j = first_true(lambda a, b: (ML[a:b] <= q['top']) & (MH[a:b] >= q['btm']), j0, len(MT))
    q['touch'] = int(MT[j]) if j >= 0 else 1 << 62
print('zones', {tf: sum(q['tf'] == tf for q in ALLZ) for tf in SEC})

# ---------- M5 ATR trail (KeyValue 2, ATR period 2), trend + last flip bar time per closed bar ----------
DSEC = SEC[P['DirTF']]
b5 = agg(DSEC); T5, H5, L5, C5 = b5['time'], b5['high'], b5['low'], b5['close']
pc = np.concatenate([[C5[0]], C5[:-1]]); TR = np.maximum(H5, pc) - np.minimum(L5, pc)
ATR = np.convolve(TR, [0.5, 0.5])[:len(TR)]; ATR[0] = TR[0]
TRD = np.ones(len(C5), dtype=np.int8); EVT = np.zeros(len(C5), dtype=np.int64); stop = C5[0]; tr = 1; ev = int(T5[0])
for i in range(len(C5)):
    src, src1 = C5[i], C5[i - 1] if i else C5[i]; prev = stop; nl = 2 * ATR[i]
    if src > prev and src1 > prev: stop = max(prev, src - nl)
    elif src < prev and src1 < prev: stop = min(prev, src + nl)
    elif src > prev: stop = src - nl
    else: stop = src + nl
    ntr = 1 if (src1 < prev and src > prev) else (-1 if (src1 > prev and src < prev) else tr)
    if ntr != tr: ev = int(T5[i])
    tr = ntr; TRD[i] = tr; EVT[i] = ev
C5T = T5 + DSEC


# ---------- context: aligning levels (>= 3 TFs H4-M5, M5 Majors only), Dynamic Zones, HTF CISD ----------
SNT, SNS, SNR = [0], [()], [()]
ev_ = []; MMB = {}
for tf, sec in (('H4', 14400), ('H2', 7200), ('H1', 3600), ('M30', 1800), ('M15', 900), ('M10', 600), ('M5', 300)):
    bb = agg(sec); MMB[tf] = (bb, MajorMinor(tf))
    ev_ += [(int(bb['time'][k]) + sec, tf, k) for k in range(len(bb['time']))]
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
def align_at(t):
    q = int(np.searchsorted(SNT, t, side='right')) - 1; return SNS[q], SNR[q]
def align_feats(d, e, t):
    """same-type level behind entry (buy: support below) distance, opposite level ahead (buy: resistance above) distance,
    opposite level just behind (buy: resistance below entry -> just broken) distance"""
    sup, res = align_at(t); lv = list(sup) + list(res)
    if d > 0:
        behind = min([e - v for v in lv if v <= e], default=999.0); ahead = min([v - e for v in lv if v > e], default=999.0)
    else:
        behind = min([v - e for v in lv if v >= e], default=999.0); ahead = min([e - v for v in lv if v < e], default=999.0)
    return behind, ahead
d1t, ZZ = session_zones(m1)
M5x = agg(300); M5ct = M5x['time'] + 300; ses5 = np.searchsorted(d1t, M5x['time'], side='right') - 1
up_t = np.full(len(d1t), np.inf); dn_t = np.full(len(d1t), np.inf)
for k in range(len(M5ct)):
    q = ses5[k]
    if q < 0 or np.isnan(ZZ[q, 1]): continue
    if M5x['close'][k] > ZZ[q, 1] and M5ct[k] < up_t[q]: up_t[q] = M5ct[k]
    if M5x['close'][k] < ZZ[q, 3] and M5ct[k] < dn_t[q]: dn_t[q] = M5ct[k]
def dz_feats(d, e, t):
    """DZ location of the entry: 2 beyond the far zone edge (Z2/Z4), 1 inside the zone, 0 middle, -1/-2 the other side
    (buy: +2 = above Z2 = resistance side). brk = +1 if an M5 close already broke out above Z2 this session, -1 below Z4."""
    q = int(np.searchsorted(d1t, t, side='right')) - 1
    if q < 0 or np.isnan(ZZ[q, 0]): return 9, 0
    Z1, Z2, Z3, Z4 = ZZ[q]
    loc = 2 if e > Z2 else 1 if e > Z1 else 0 if e >= Z3 else -1 if e >= Z4 else -2
    brk = (1 if up_t[q] <= t else 0) - (1 if dn_t[q] <= t else 0)
    return loc, brk
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
HTF = {}
for tf, sec in (('M15', 900), ('M30', 1800), ('H1', 3600), ('H2', 7200), ('H4', 14400)):
    bb = agg(sec); cs = cisd_lux(bb['open'], bb['close']); nz = np.flatnonzero(cs)
    HTF[tf] = ((bb['time'][nz] + sec).astype(np.int64), cs[nz])
def htf_dir(tf, t):
    ct, dv = HTF[tf]; q = int(np.searchsorted(ct, t, side='right')) - 1
    return int(dv[q]) if q >= 0 else 0
NOH = set()
if P['NoHours']:
    for part in P['NoHours'].split('+'):
        a_, b_ = (int(x) for x in part.split('-')); NOH |= set(range(a_, b_ + 1))
ist_h = lambda t: ((t + 19800) // 3600) % 24
ist_day = lambda t: (t + 19800) // 86400
MODES = set(P['Modes'].split(',')); SKIP = set(P['Skip'].split(',')) if P['Skip'] else set()

# ---------- replay ----------
active = {(tf, d): [] for tf in SEC for d in (1, -1)}
zi = 0; nextmit = 1 << 62
dn_t_ok = lambda t: True
daycount = {}
traded = set(); blocked = {}; floor = {}; dblock = {}
pend = None; pos = None; trades = []; cnt = dict(market=0, pending=0, filled=0, cancel_inel=0, cancel_gone=0, replace=0,
                                                 rejected=0, square=0)
def hist(tf, d): return active[(tf, d)][:P['Show']]
def h0(tf, d):
    h = active[(tf, d)]; return h[0] if h else None
def select_sl(d, ref, edges):
    v = [e for e in edges if e is not None and ((d > 0 and e < ref) or (d < 0 and e > ref))]
    if not v: return None
    e = min(v, key=lambda e: abs(e - ref)); return e - 0.5 if d > 0 else e + 0.5
def edge(tf, d):
    q = h0(tf, d); return None if q is None else (q['btm'] if d > 0 else q['top'])
def open_pos(t, d, src, mode, entry, sl, ext, ev):
    global pos
    behind, ahead = align_feats(d, entry, t); loc, brk = dz_feats(d, entry, t); risk = abs(entry - sl); tp = None
    if P['TP'].startswith('R'): tp = entry + d * float(P['TP'][1:]) * risk
    elif P['TP'] == 'align' and ahead < 999: tp = entry + d * max(ahead - P['TPBuf'], 0.5)
    f = dict(h=int(ist_h(t)), wd=int(((t + 19800) // 86400 + 4) % 7), behind=behind, ahead=ahead, loc=loc * d, brk=brk * d,
             m15=htf_dir('M15', t) * d, h1=htf_dir('H1', t) * d, h4=htf_dir('H4', t) * d, atr=int(TRD[k5]) * d,
             zfav=int(zdir == d), zsrc='ATR' if zev == int(EVT[k5]) else 'OB', age=(t - ev) / 60.0,
             nday=daycount.get(ist_day(t), 0))
    daycount[ist_day(t)] = daycount.get(ist_day(t), 0) + 1
    pos = dict(t=t, d=d, src=src, mode=mode, entry=entry, sl=sl, sl0=sl, tp=tp, ext=ext, mfe=0.0, seen=False, f=f)
def close_pos(k, why):
    global pos
    px = BID[k] if pos['d'] > 0 else ASK[k]
    pts = (px - pos['entry']) * pos['d'] - P['Comm']; xt = int(TT[k] // 1000)
    if pos['t'] >= START:
        trades.append(dict(t=pos['t'], x=xt, d=pos['d'], src=pos['src'], mode=pos['mode'], entry=pos['entry'], exit=float(px),
                           risk=abs(pos['entry'] - pos['sl0']), pts=float(pts), why=why, mfe=float(pos['mfe']), **pos['f']))
    if why in ('sl', 'tp'): dblock[pos['d']] = xt
    pos = None

i0 = int(np.searchsorted(MT, START - 3 * 86400)); iend = int(np.searchsorted(MT, END))
for i in range(i0, iend):
    now = int(MT[i]); a, b = int(TI[i]), int(TI[i + 1])
    if a >= b: continue
    while zi < len(ALLZ) and ALLZ[zi]['formed'] <= now:
        q = ALLZ[zi]; active[(q['tf'], q['d'])].insert(0, q); zi += 1; nextmit = min(nextmit, q['mit'])
    if now >= nextmit:
        nextmit = 1 << 62
        for kk in active:
            active[kk] = [q for q in active[kk] if q['mit'] > now]
            for q in active[kk]: nextmit = min(nextmit, q['mit'])
    k5 = int(np.searchsorted(C5T, now, side='right')) - 1
    if k5 < 0: continue
    zc = [(int(EVT[k5]), int(TRD[k5]))]
    for d in (1, -1):
        q = h0(P['DirTF'], d)
        if q: zc.append((q['start'], d))
    zev, zdir = max(zc, key=lambda c: c[0])
    cur = float(MID[a])

    # block releases
    for tf in list(blocked):
        nw = [q['start'] for d in (1, -1) for q in hist(tf, d)]
        if nw and max(nw) > int(blocked[tf].split('|')[2]): del blocked[tf]
    for d in list(dblock):
        nw = [h0(tf, d)['start'] for tf in ('M1', 'M3', 'M5') if h0(tf, d)]
        if nw and max(nw) > dblock[d]: del dblock[d]

    def ctx_ok(c):
        if c['mode'] not in MODES or c['src'] + c['mode'] in SKIP: return False
        if NOH and ist_h(now) in NOH: return False
        if P['MaxDay'] and daycount.get(ist_day(now), 0) >= P['MaxDay']: return False
        d = c['d']; e = c['entry'] if c['entry'] is not None else cur; risk = abs(e - c['sl'])
        if P['MinRisk'] and risk < P['MinRisk']: return False
        if P['MaxRisk'] and risk > P['MaxRisk']: return False
        if P['ATRAgree'] and int(TRD[k5]) != d: return False
        if P['HTF'] and any(htf_dir(h_, now) != d for h_ in P['HTF'].split('+')): return False
        if P['Room'] or P['AlignAt']:
            behind, ahead = align_feats(d, e, now)
            if P['Room'] and ahead < P['Room']: return False
            if P['AlignAt'] and behind > P['AlignAt']: return False
        if P['DZF'] or P['DZLoc'] or P['DZDeep']:
            loc, brk = dz_feats(d, e, now)
            if P['DZF'] and ((d > 0 and brk < 0 and dn_t_ok(now)) or (d < 0 and brk > 0)): return False
            if P['DZLoc'] and loc * d >= P['DZLoc'] and loc != 9: return False
            if P['DZDeep'] and loc * d <= -2 and loc != 9: return False
        return True
    def elig(c, strict):
        if not ctx_ok(c): return False
        fl = floor.get((c['src'], c['d']))
        if fl is not None and c['ev'] <= fl: return False
        if c['key'] in traded or blocked.get(c['src']) == c['key'] or c['d'] in dblock: return False
        if c['d'] == zdir: return True
        return (not strict) and c['ev'] >= zev
    cands = []
    for d in (1, -1):
        sledges = [edge('M15', d), edge('M5', d), edge('M3', d)]
        if 'M1' in SRC:
            al = sorted([q for dd in (1, -1) for q in hist('M1', dd)], key=lambda q: -q['start'])
            if len(al) >= 2 and al[0]['d'] == d and al[1]['d'] == d and now <= al[0]['touch']:
                q = al[0]; en = q['top'] + 0.25 if d > 0 else q['btm'] - 0.25; sl = select_sl(d, en, sledges)
                c = dict(src='M1', d=d, mode='P', entry=en, sl=sl, ev=q['start'], key=q['key'])
                if sl is not None and elig(c, True): cands.append(c)
        for tf in ('M3', 'M5', 'M15', 'M30'):
            if tf not in SRC: continue
            q = h0(tf, d)
            if not q or now > q['touch']: continue
            e = q['top'] if d > 0 else q['btm']; dist = (q['det'] - e) * d
            if dist < 0: continue
            if dist <= 4: mode, en = 'M', None
            elif 4 < dist < 12: mode, en = 'P', e + d * max(dist * 0.55, 4.0)
            else: continue
            sl = select_sl(d, en if en is not None else cur, sledges + ([edge('M30', d)] if tf == 'M30' else []))
            c = dict(src=tf, d=d, mode=mode, entry=en, sl=sl, ev=q['start'], key=q['key'])
            if sl is not None and elig(c, tf == 'M3'): cands.append(c)
    dist_of = lambda c: 0.0 if c['entry'] is None else abs(c['entry'] - cur)
    win = min(cands, key=lambda c: (dist_of(c), -c['ev'])) if cands else None

    if pos and P['Square'] and win and win['d'] != pos['d']:
        cnt['square'] += 1; close_pos(a, 'square')
    if pend:
        strict = pend['src'] in ('M1', 'M3')
        inel = not (pend['d'] == zdir or ((not strict) and pend['ev'] >= zev))
        gone = not any(q['key'] == pend['key'] for q in active[(pend['src'], pend['d'])])
        if inel or gone:
            cnt['cancel_inel' if inel else 'cancel_gone'] += 1
            blocked[pend['src']] = pend['key']; fk = (pend['src'], pend['d']); floor[fk] = max(floor.get(fk, 0), pend['ev'])
            pend = None
    if pos and P['Trail'] and pos['seen']:
        oc = select_sl(pos['d'], cur, [edge('M15', pos['d']), edge('M5', pos['d'])])
        if oc is not None and (oc - pos['sl']) * pos['d'] > 1e-6: pos['sl'] = oc
    if pos: pos['seen'] = True
    if win and not pos:
        place = pend is None or (win['key'] != pend['key'] and dist_of(win) < abs(pend['entry'] - cur))
        if place:
            if pend: cnt['replace'] += 1; pend = None
            if win['mode'] == 'M':
                px = ASK[a] if win['d'] > 0 else BID[a]
                if (px - win['sl']) * win['d'] > 0:
                    cnt['market'] += 1; traded.add(win['key'])
                    open_pos(now, win['d'], win['src'], 'M', float(px), win['sl'], float(MID[a]), win['ev'])
                else: cnt['rejected'] += 1
            else:
                ok = (win['entry'] < ASK[a]) if win['d'] > 0 else (win['entry'] > BID[a])
                if ok: pend = dict(win); cnt['pending'] += 1
                else: cnt['rejected'] += 1

    # ---- ticks inside this minute ----
    k = a
    if pend and not pos:
        d = pend['d']
        hit = np.flatnonzero(ASK[a:b] <= pend['entry']) if d > 0 else np.flatnonzero(BID[a:b] >= pend['entry'])
        if len(hit):
            k = a + int(hit[0]); cnt['filled'] += 1; traded.add(pend['key'])
            open_pos(int(TT[k] // 1000), d, pend['src'], 'P', float(pend['entry']), pend['sl'], float(pend['entry']), pend['ev'])
            pend = None
    if pos and k < b:
        d = pos['d']; m = MID[k:b]
        if d > 0: ext = np.maximum.accumulate(np.concatenate([[pos['ext']], m]))[:-1]; fav = ext - pos['entry']
        else: ext = np.minimum.accumulate(np.concatenate([[pos['ext']], m]))[:-1]; fav = pos['entry'] - ext
        slp = np.full(len(m), pos['sl'])
        if P['Trail']:
            pt = np.where(fav > 10, ext - 10 * d, np.where(fav >= 7, pos['entry'], -np.inf * d))
            slp = np.maximum(slp, pt) if d > 0 else np.minimum(slp, pt)
        hs = (BID[k:b] <= slp) if d > 0 else (ASK[k:b] >= slp)
        ht = np.zeros(len(m), dtype=bool) if pos['tp'] is None else ((BID[k:b] >= pos['tp']) if d > 0 else (ASK[k:b] <= pos['tp']))
        hit = np.flatnonzero(hs | ht)
        if len(hit):
            j = int(hit[0]); pos['mfe'] = max(pos['mfe'], float(fav[:j + 1].max())); close_pos(k + j, 'sl' if hs[j] else 'tp')
        else:
            pos['mfe'] = max(pos['mfe'], float(fav.max()), float(((m[-1] - pos['entry']) * d)))
            pos['ext'] = float(max(ext[-1], m[-1]) if d > 0 else min(ext[-1], m[-1])); pos['sl'] = float(slp[-1])
            if P['Trail']:
                f = (pos['ext'] - pos['entry']) * d
                if f > 10: pos['sl'] = max(pos['sl'], pos['ext'] - 10) if d > 0 else min(pos['sl'], pos['ext'] + 10)
                elif f >= 7: pos['sl'] = max(pos['sl'], pos['entry']) if d > 0 else min(pos['sl'], pos['entry'])

# ---------- report ----------
def rep(tr, lab):
    if not tr: print(f"  {lab:10s} no trades"); return {}
    eq = pk = dd = 0.0; mon = {}
    for x in sorted(tr, key=lambda x: x['x']):
        eq += x['pts']; pk = max(pk, eq); dd = max(dd, pk - eq)
        mm = dt.datetime.fromtimestamp(x['x'], U).strftime('%y-%m'); mon[mm] = mon.get(mm, 0) + x['pts']
    print(f"  {lab:10s} trades {len(tr):5d}  win {sum(x['pts'] > 0 for x in tr) / len(tr) * 100:5.1f}%  net {eq:+8.1f} pts  "
          f"drop {dd:6.1f}  losing months {sum(v < 0 for v in mon.values()):2d}/{len(mon)}  avg risk {np.mean([x['risk'] for x in tr]):5.2f}  "
          f"avg win {np.mean([x['pts'] for x in tr if x['pts'] > 0] or [0]):5.2f}  avg loss {np.mean([x['pts'] for x in tr if x['pts'] <= 0] or [0]):6.2f}  "
          f"avg hold {np.mean([x['x'] - x['t'] for x in tr]) / 60:5.1f}m")
    return mon
SPL = ts('2025-11-01')
flt = ' '.join(f"{k}={P[k]}" for k in ('Skip', 'DirTF', 'TP', 'Room', 'AlignAt', 'DZF', 'DZLoc', 'DZDeep', 'Square', 'Src', 'NoHours', 'MinRisk', 'MaxRisk', 'MaxDay', 'HTF', 'ATRAgree') if P[k])
if P['Modes'] != 'M,P': flt += f" Modes={P['Modes']}"
print(f"algo_v2 replay [{flt or 'base'}] | sources {P['Src']} | trail {'on' if P['Trail'] else 'off'} | square-off {'on' if P['Square'] else 'off'} | "
      f"{P['Start']} -> {P['End']} | {cnt}")
mon = rep(trades, 'ALL')
rep([x for x in trades if x['t'] < SPL], 'Nov24-Oct25'); rep([x for x in trades if x['t'] >= SPL], 'Nov25-Oct26')
if P['Quiet']:
    if mon: print('  months: ' + ' '.join(f"{k} {v:+.0f}" for k, v in mon.items()))
    if P['TrOut']: json.dump(trades, open(P['TrOut'], 'w'), default=float)
    sys.exit()
for s in ('M1', 'M3', 'M5', 'M15', 'M30'): rep([x for x in trades if x['src'] == s], s)
rep([x for x in trades if x['mode'] == 'M'], 'market'); rep([x for x in trades if x['mode'] == 'P'], 'pending')
rep([x for x in trades if x['d'] > 0], 'BUY'); rep([x for x in trades if x['d'] < 0], 'SELL')
rep([x for x in trades if x['why'] == 'sl'], 'exit SL'); rep([x for x in trades if x['why'] == 'square'], 'exit square')
rep([x for x in trades if x['why'] == 'tp'], 'exit TP')
if mon: print('  months: ' + ' '.join(f"{k} {v:+.0f}" for k, v in mon.items()))
if P['TrOut']: json.dump(trades, open(P['TrOut'], 'w'), default=float)
