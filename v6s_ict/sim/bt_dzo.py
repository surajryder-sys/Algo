"""Standalone Dynamic Zones test: reversals from / breakouts of the DZ resistance (Z1-Z2) and support (Z3-Z4) zones,
CISD entries on the TF candle close, SL at the touch swing (or swing below/above the zone for breakouts) +/- SLBuf,
fixed TP at RR x risk. One trade at a time per book. Books: {REV, BO, BOTH} x RR list."""
import sys, numpy as np, datetime as dt
from v6sim import agg, MajorMinor, TFS
from ohlc import synth_ticks
import ohlc; ohlc.SPREAD = 0.0   # oil: real spread comes from MinSpread

P = dict(TF=300, SLBuf=2.0, Lot=0.10, Tol=0.7, Contract=100.0, Comm=3.5, MinSpread=0.20, SwapLongPerLot=-55.04,
         TickSrc='real', SQ=0, ONE=0, NOCISD=0, TrOut='', CapMinTPR=0.0, MinTPPts=0.0, Data='m1_2y.npy', NoLondon=0, SwapShortPerLot=0.0, WkCut=0, WkNoEntry=3600, BE=0.5, TPBuf=1.0, Warm=3000, MinTF=3, MinMajor=2, MaxSL=0.0, MaxRisk=0.0, BOSL='swing', RR='2,3,4', Modes='REV,BO,BOTH', Start='2026-04-01', End='2026-09-25', Tag='')
for a in sys.argv[1:]:
    k, v = a.split('='); P[k] = type(P[k])(v) if not isinstance(P[k], str) else v
RRS = tuple(float(x) for x in P['RR'].split(','))
IST = dt.timedelta(hours=5, minutes=30)
def istm(t): return (dt.datetime.fromtimestamp(int(t), dt.timezone.utc) + IST).strftime('%Y-%m')
def ists(t): return (dt.datetime.fromtimestamp(int(t), dt.timezone.utc) + IST).strftime('%d %b %y %H:%M')

m1 = np.load(P['Data'])
start = int(np.datetime64(P['Start']).astype('datetime64[s]').astype(np.int64))
end = int(np.datetime64(P['End']).astype('datetime64[s]').astype(np.int64))
ticks = np.load('ticks.npy') if P['TickSrc'] == 'real' else synth_ticks(m1[(m1['time'] >= start - 86400) & (m1['time'] < end + 86400 * 30)])
tk = ticks[(ticks[:, 0] >= start * 1000)]
TT = tk[:, 0] / 1000.0; BID = tk[:, 1].copy(); ASK = np.maximum(tk[:, 2], BID + P['MinSpread'])

# zones per server D1
D1 = agg(m1, 86400); d1t = D1['time']; rng = D1['high'] - D1['low']
Z = np.full((len(D1), 4), np.nan)
for k in range(10, len(D1)):
    h5 = rng[k-5:k].mean() / 2; h10 = rng[k-10:k].mean() / 2; o = D1['open'][k]
    Z[k] = (o + h5, o + h10, o - h5, o - h10)

B = agg(m1, P['TF']); sec = P['TF']
O, H, L, C = B['open'], B['high'], B['low'], B['close']
# ---- CISD (AlgoAlpha, same as v6run)
bear, bull = [], []
def cisd(i):
    o, c = O[i], C[i]
    if i >= 1:
        if C[i-1] < O[i-1] and c > o: bear.insert(0, (o, i))
        if C[i-1] > O[i-1] and c < o: bull.insert(0, (o, i))
    r = 0
    while bear:
        co, ci = bear[0]
        if C[i] < co:
            highest = max(0.0, C[ci:i+1].max()); top = 0.0; k = ci - 1
            while k >= 0 and C[k] < O[k]: top = O[k]; k -= 1
            d = top - co
            if d != 0 and (highest - co) / d > P['Tol']: bear.clear(); r = -1; break
            bear.pop(0)
        else: break
    while bull:
        co, ci = bull[0]
        if C[i] > co:
            lowest = C[ci:i+1].min(); bottom = 0.0; k = ci - 1
            while k >= 0 and C[k] > O[k]: bottom = O[k]; k -= 1
            d = co - bottom
            if d != 0 and (co - lowest) / d > P['Tol']: bull.clear(); r = 1; break
            bull.pop(0)
        else: break
    return r

def fri_cut(t):
    # the next Friday WkCut (UTC) at or after t
    day = int(t // 86400); wdn = (day + 3) % 7
    c = (day + (4 - wdn) % 7) * 86400 + P['WkCut']
    return c if c >= t else c + 7 * 86400
def exit_scan(j0, d, sl, tp):
    """first tick at/after j0 where SL or TP is hit (or the Friday cut-off); returns (j, price, reason)"""
    j = j0; n = len(TT); why_end = 'OPEN'
    if P['WkCut']:
        jc = int(np.searchsorted(TT, fri_cut(TT[j0])))
        if jc < n: n = jc + 1; why_end = 'WEEKEND'
    while j < n:
        e = min(n, j + 200000)
        px = BID[j:e] if d > 0 else ASK[j:e]
        hit = (px <= sl) | (px >= tp) if d > 0 else (px >= sl) | (px <= tp)
        w = np.flatnonzero(hit)
        if len(w):
            k = j + w[0]; p = px[w[0]]
            slhit = p <= sl if d > 0 else p >= sl
            return k, p, 'SL' if slhit else 'TP'
        j = e
    return n - 1, (BID[n-1] if d > 0 else ASK[n-1]), why_end

def exit_scan_be(j0, d, entry, sl, tp, be_at):
    """SL/TP, and once price reaches be_at the SL moves to entry"""
    if be_at is None: return exit_scan(j0, d, sl, tp)
    j = j0; n = len(TT); why_end = 'OPEN'
    if P['WkCut']:
        jc = int(np.searchsorted(TT, fri_cut(TT[j0])))
        if jc < n: n = jc + 1; why_end = 'WEEKEND'
    while j < n:
        e = min(n, j + 200000)
        px = BID[j:e] if d > 0 else ASK[j:e]
        hit = (px <= sl) | (px >= be_at) if d > 0 else (px >= sl) | (px <= be_at)
        w = np.flatnonzero(hit)
        if len(w):
            k = j + w[0]; p = px[w[0]]
            if (p <= sl if d > 0 else p >= sl): return k, p, 'SL'
            k2, p2, why = exit_scan(k, d, entry, tp)
            return k2, p2, ('BE-STOP' if why == 'SL' else why)
        j = e
    return n - 1, (BID[n-1] if d > 0 else ASK[n-1]), why_end

# ---- aligning Major/Minor levels (same rules as V6S-ICT: 3+ TFs, 2+ Major)
mbars = {name: agg(m1, s_) for name, s_ in TFS}; mms = {name: MajorMinor(name) for name, _ in TFS}; mptr = {}
for name, s_ in TFS:
    b = mbars[name]; closed = np.flatnonzero(b['time'] + s_ <= start - 20 * 86400)
    for k in range(max(0, closed[-1] - P['Warm'] + 1), closed[-1] + 1):
        mms[name].add(b['high'][k], b['low'][k], b['close'][k], int(b['time'][k]))
    mptr[name] = closed[-1] + 1
ALS, ALR = [], []
def advance_levels(T):
    global ALS, ALR
    ch = False
    for name, s_ in TFS:
        b = mbars[name]
        while mptr[name] < len(b) and b['time'][mptr[name]] + s_ <= T:
            k = mptr[name]; mms[name].add(b['high'][k], b['low'][k], b['close'][k], int(b['time'][k])); mptr[name] += 1; ch = True
    if not ch: return
    sd, rd = {}, {}
    for name, _ in TFS:
        for side, v, kind in mms[name].levels():
            (sd if side == 'S' else rd).setdefault(v, set()).add((name, kind))
    ok = lambda mem: len({m[0] for m in mem}) >= P['MinTF'] and len({m[0] for m in mem if m[1] == 'Maj'}) >= P['MinMajor']
    ALS = [v for v, mem in sd.items() if ok(mem)]; ALR = [v for v, mem in rd.items() if ok(mem)]

def swap_cost(t0, t1, d=1):
    s = 0.0; d0 = int(t0 // 86400); d1 = int(t1 // 86400)
    for last_day in range(d0, d1):
        s += (P['SwapLongPerLot'] if d > 0 else P['SwapShortPerLot']) * P['Lot'] * (3 if (last_day + 3) % 7 == 2 else 1)
    return s

EXITS = [('LEVEL', False), ('LVL>=1R', False), ('1:1', False), ('1:1.5', False), ('1:2', False), ('1:3', False)]
books = {(m, x): dict(busy=0.0, trades=[], fb=0) for m in P['Modes'].split(',') for x in EXITS}
signals = {'REV': 0, 'BO': 0}
# setup state (reset each new D1 session)
dk_cur = -1
rs = dict(tS=-1, tR=-1)     # reversal: first touch bar of the current touch run (support / resistance)
bo = dict(up=-1, dn=-1, usedU=False, usedD=False)     # breakout: bar of the close through the zone
last_below = -1; last_above = -1   # last bar that traded below the resistance zone / above the support zone

i0 = int(np.searchsorted(B['time'], start - 20 * 86400))
for i in range(i0, len(B)):
    bt = B['time'][i]; T = bt + sec
    if T >= end: break
    r = cisd(i)
    advance_levels(T)
    dk = int(np.searchsorted(d1t, bt, side='right') - 1)
    if dk < 0 or np.isnan(Z[dk, 0]): continue
    z1, z2, z3, z4 = Z[dk]; rLo, rHi = min(z1, z2), max(z1, z2); sLo, sHi = min(z3, z4), max(z3, z4)
    if dk != dk_cur:
        dk_cur = dk; rs['tS'] = rs['tR'] = -1; bo['up'] = bo['dn'] = -1; bo['usedU'] = bo['usedD'] = False
    # --- update touches / breaks on this closed candle
    if H[i] >= rLo and rs['tR'] < 0: rs['tR'] = i
    if L[i] <= sHi and rs['tS'] < 0: rs['tS'] = i
    if C[i] > rHi: rs['tR'] = -1                      # closed above the resistance zone: no longer a reversal setup
    if C[i] < sLo: rs['tS'] = -1
    if C[i] <= rHi: bo['usedU'] = False               # back inside/below the zone: a new break can trade again
    if C[i] >= sLo: bo['usedD'] = False
    if C[i] > rHi and bo['up'] < 0 and not (P['ONE'] and bo['usedU']): bo['up'] = i
    if C[i] < sLo and bo['dn'] < 0 and not (P['ONE'] and bo['usedD']): bo['dn'] = i
    if bo['up'] >= 0 and C[i] < rHi: bo['up'] = -1     # closed back into / below the zone: break cancelled
    if bo['dn'] >= 0 and C[i] > sLo: bo['dn'] = -1
    if L[i] < rLo: last_below = i
    if H[i] > sHi: last_above = i
    if T < start: continue
    sig = []
    # reversals: touch (this or an earlier candle today), CISD closing back outside the zone
    if r < 0 and rs['tR'] >= 0 and C[i] < rLo:
        sig.append(('REV', -1, H[rs['tR']:i+1].max() + P['SLBuf'], f"SELL from R zone {rLo:.2f}-{rHi:.2f}")); rs['tR'] = -1
    if r > 0 and rs['tS'] >= 0 and C[i] > sHi:
        sig.append(('REV', 1, L[rs['tS']:i+1].min() - P['SLBuf'], f"BUY from S zone {sLo:.2f}-{sHi:.2f}")); rs['tS'] = -1
    # breakouts: close beyond the zone, CISD (same or later candle) closing beyond it
    if (C[i] > rHi and C[i-1] <= rHi) if P['NOCISD'] else (r > 0 and bo['up'] >= 0 and C[i] > rHi):
        s0 = last_below if 0 <= last_below <= bo['up'] else bo['up']
        sig.append(('BO', 1, (rLo if P['BOSL'] == 'zone' else L[s0:i+1].min()) - P['SLBuf'], f"BREAKOUT of R zone {rLo:.2f}-{rHi:.2f}")); bo['up'] = -1; bo['usedU'] = True
    if (C[i] < sLo and C[i-1] >= sLo) if P['NOCISD'] else (r < 0 and bo['dn'] >= 0 and C[i] < sLo):
        s0 = last_above if 0 <= last_above <= bo['dn'] else bo['dn']
        sig.append(('BO', -1, (sHi if P['BOSL'] == 'zone' else H[s0:i+1].max()) + P['SLBuf'], f"BREAKDOWN of S zone {sLo:.2f}-{sHi:.2f}")); bo['dn'] = -1; bo['usedD'] = True
    if not sig: continue
    j0 = int(np.searchsorted(TT, T))
    if j0 >= len(TT): break
    for typ, d, sl, desc in sig:
        signals[typ] += 1
        entry = ASK[j0] if d > 0 else BID[j0]
        if P['SQ']:
            for bk in books.values():
                lt_ = bk['trades'][-1] if bk['trades'] else None
                if lt_ is not None and TT[j0] < bk['busy'] and lt_['d'] == -d:
                    px = BID[j0] if lt_['d'] > 0 else ASK[j0]
                    pl = (px - lt_['entry']) * lt_['d'] * P['Lot'] * P['Contract'] - P['Comm'] * P['Lot'] * 2
                    pl += swap_cost(lt_['t'], TT[j0], lt_['d'])
                    lt_.update(te=TT[j0], pl=pl, R=(px - lt_['entry']) * lt_['d'] / lt_['risk'], why='SQUARE-OFF')
                    bk['busy'] = TT[j0]
        if P['WkCut'] and ((int(T) // 86400) + 3) % 7 == 4 and int(T) % 86400 >= P['WkCut'] - P['WkNoEntry']: continue   # no new entries before the weekend
        if P['NoLondon'] and 750 <= ((int(T) + 19800) % 86400) // 60 < 1110: signals['london'] = signals.get('london', 0) + 1; continue   # no NEW entries 12:30-18:30 IST (square-off above still applies)
        if P['MaxRisk'] > 0 and typ == 'BO' and (entry - sl) * d > P['MaxRisk']: signals['skipped'] = signals.get('skipped', 0) + 1; continue
        capped = P['MaxSL'] > 0 and (entry - sl) * d > P['MaxSL']
        if capped: sl = entry - d * P['MaxSL']
        risk = (entry - sl) * d
        if risk <= 0: continue
        if d > 0: lv = [v - P['TPBuf'] for v in ALR if v - P['TPBuf'] > entry]; lt = min(lv) if lv else None
        else:     lv = [v + P['TPBuf'] for v in ALS if v + P['TPBuf'] < entry]; lt = max(lv) if lv else None
        for (m, (xn, xbe)), bk in books.items():
            if m != 'BOTH' and m != typ: continue
            if TT[j0] < bk['busy']: continue
            if xn.startswith('1:'): tp = entry + d * float(xn[2:]) * risk
            elif lt is not None and (xn == 'LEVEL' or (lt - entry) * d >= risk): tp = lt
            else: tp = entry + d * risk; bk['fb'] += 1        # no level (or under 1R for LVL>=1R): 1:1
            if P['MinTPPts'] > 0 and lt is not None and (lt - entry) * d < P['MinTPPts']: continue
            if capped and P['CapMinTPR'] > 0 and (tp - entry) * d < P['CapMinTPR'] * risk: continue
            je, px, why = exit_scan_be(j0, d, entry, sl, tp, entry + d * P['BE'] * risk if xbe else None)
            pl = (px - entry) * d * P['Lot'] * P['Contract'] - P['Comm'] * P['Lot'] * 2
            pl += swap_cost(TT[j0], TT[je], d)
            bk['trades'].append(dict(t=TT[j0], te=TT[je], typ=typ, d=d, entry=entry, sl=sl, risk=risk, pl=pl,
                                     R=(px - entry) * d / risk, why=why, desc=desc, tpR=(tp - entry) * d / risk))
            bk['busy'] = TT[je]

tf = {300: 'M5', 900: 'M15'}.get(sec, str(sec))
print(f"=== DZ test {tf} {P['Start']}..{P['End']} ticks={P['TickSrc']} lot {P['Lot']} SL buffer {P['SLBuf']} | signals {signals}")
for (m, (xn, xbe)), bk in books.items():
    tr = bk['trades']; rr = 0
    if not tr: print(f"{tf} {m} {xn} no trades"); continue
    ex = {}
    for t in tr: ex[t['why']] = ex.get(t['why'], 0) + 1
    pl = np.array([t['pl'] for t in tr]); eq = np.cumsum(pl); peak = np.maximum.accumulate(np.concatenate([[0], eq]))[1:]
    dd = (peak - eq).max(); low = min(0, eq.min())
    mon = {}
    for t in tr: mon[istm(t['t'])] = mon.get(istm(t['t']), 0) + t['pl']
    wins = sum(1 for t in tr if t['pl'] > 0)
    print(f"{tf} {m} {xn:10s} exits {ex} TP median {np.median([t['tpR'] for t in tr]):.2f}R  fallback 1:1 {bk['fb']}")
    print(f"{tf} {m:4s} {xn:10s} trades {len(tr):4d}  win {100*wins/len(tr):4.1f}%  net {pl.sum():+9.2f}  R {sum(t['R'] for t in tr):+7.1f}"
          f"  drop {dd:8.2f}  low {low:+8.2f}  median risk {np.median([t['risk'] for t in tr]):5.2f}  max risk {max(t['risk'] for t in tr):6.2f}"
          f"  losing months {sum(1 for v in mon.values() if v < 0)}/{len(mon)}  worst trade {pl.min():+.0f}")
    print("   MONTHS " + "  ".join(f"{k} {v:+.0f}" for k, v in sorted(mon.items())))

import json as _j
if P['TrOut']: _j.dump([dict(t=float(x['t']), te=float(x['te']), pl=float(x['pl']), src='DZ', typ=1) for x in books[('BO', ('LEVEL', False))]['trades']], open(P['TrOut'], 'w'))

if P['TrOut']:
    _j.dump({xn: [dict(t=float(x['t']), te=float(x['te']), pl=float(x['pl']), pts=float(x['R'] * x['risk']), risk=float(x['risk']), d=int(x['d']), why=x['why']) for x in bk['trades']] for (m, (xn, xbe)), bk in books.items()}, open(P['TrOut'], 'w'))
