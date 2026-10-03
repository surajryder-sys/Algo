"""Standalone Dynamic Zones test: reversals from / breakouts of the DZ resistance (Z1-Z2) and support (Z3-Z4) zones,
CISD entries on the TF candle close, SL at the touch swing (or swing below/above the zone for breakouts) +/- SLBuf,
fixed TP at RR x risk. One trade at a time per book. Books: {REV, BO, BOTH} x RR list."""
import sys, numpy as np, datetime as dt
from v6sim import agg, MajorMinor, TFS
from ohlc import synth_ticks

P = dict(MinADR=0.0, ADRDays=10, TF=300, SLBuf=2.0, Lot=0.10, Tol=0.7, Contract=100.0, Comm=3.5, MinSpread=0.20, SwapLongPerLot=-55.04,
         TickSrc='real', SQ=0, ONE=0, NOCISD=0, TrOut='', CapMinTPR=0.0, MinTPPts=0.0, Data='m1_2y.npy', NoLondon=0, DZLeg=0, DZLegTP=2.0, BE=0.5, TPBuf=1.0, Warm=3000, MinTF=3, MinMajor=2, MaxSL=0.0, MaxRisk=0.0, BOSL='swing', RR='2,3,4', Modes='REV,BO,BOTH', Start='2026-04-01', End='2026-09-25', Tag='', LevelTFs='H4,H2,H1,M30,M15,M10,M5,M3', Ticks='ticks.npy', CISD='algo', LuxMax=100, ExitAlgo=0, REVSL='touch', REVBuf=4.0, ZoneMode='gap', SwapTriple=1, MaxDay=0, TrendF=0, MinRiskZ=0.0, DZNight=0, DZBER=0.0, FriNight=0, Win='')
for a in sys.argv[1:]:
    k, v = a.split('='); P[k] = type(P[k])(v) if not isinstance(P[k], str) else v
RRS = tuple(float(x) for x in P['RR'].split(','))
IST = dt.timedelta(hours=5, minutes=30)
def istm(t): return (dt.datetime.fromtimestamp(int(t), dt.timezone.utc) + IST).strftime('%Y-%m')
def ists(t): return (dt.datetime.fromtimestamp(int(t), dt.timezone.utc) + IST).strftime('%d %b %y %H:%M')

m1 = np.load(P['Data'])
start = int(np.datetime64(P['Start']).astype('datetime64[s]').astype(np.int64))
end = int(np.datetime64(P['End']).astype('datetime64[s]').astype(np.int64))
ticks = np.load(P['Ticks']) if P['TickSrc'] == 'real' else synth_ticks(m1[(m1['time'] >= start - 86400) & (m1['time'] < end + 86400 * 30)])
tk = ticks[(ticks[:, 0] >= start * 1000)]
TT = tk[:, 0] / 1000.0; BID = tk[:, 1].copy(); ASK = np.maximum(tk[:, 2], BID + P['MinSpread'])

# zones per server D1
_DQ = agg(m1, 86400); _DQ = _DQ[((_DQ['time'] // 86400 + 3) % 7) != 6]; _DQr = _DQ['high'] - _DQ['low']   # Sunday stub bars excluded
def QUIET(t):    # MinADR (v2.23): no new entries while the average daily range of the last ADRDays closed days is below it
    if P['MinADR'] <= 0: return False
    k = int(np.searchsorted(_DQ['time'], t, side='right') - 1)
    return k >= P['ADRDays'] and _DQr[k-P['ADRDays']:k].mean() < P['MinADR']
from sessz import session_zones, day_zones
_D = agg(m1, 86400)
def trend_at(t):    # +1 if the last closed day closed above its 20-day average, else -1
    k = int(np.searchsorted(_D['time'], t, side='right') - 1)
    if k < 2: return 0
    return 1 if _D['close'][k-1] > _D['close'][max(0, k-20):k].mean() else -1
d1t, Z = day_zones(m1) if P['ZoneMode'] == 'd1' else session_zones(m1)          # gap-delimited sessions, as the indicator / EA v2.13

B = agg(m1, P['TF']); sec = P['TF']
O, H, L, C = B['open'], B['high'], B['low'], B['close']
# ---- CISD (AlgoAlpha, same as v6run)
bear, bull = [], []
class Lux:      # LuxAlgo CISD, Classic method (same as EA tester v2.16)
    def __init__(s): s.bl = None; s.br = None
    def step(s, O, C, i):
        if i >= 1:
            if C[i] > O[i] and C[i-1] < O[i-1]: s.bl = [O[i], i]
            if C[i] < O[i] and C[i-1] > O[i-1]: s.br = [O[i], i]
        r = 0
        if s.bl is not None:
            if i - s.bl[1] > P['LuxMax']: s.bl = None
            elif C[i] < s.bl[0]: r = -1; s.bl = None
        if s.br is not None:
            if i - s.br[1] > P['LuxMax']: s.br = None
            elif C[i] > s.br[0]: r = 1; s.br = None
        return r
luxz = Lux()
def cisd(i):
    if P['CISD'] == 'lux': return luxz.step(O, C, i)
    return cisd_algo(i)
def cisd_algo(i):
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

def exit_scan(j0, d, sl, tp):
    """first tick at/after j0 where SL or TP is hit; returns (j, price, reason)"""
    j = j0; n = len(TT)
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
    return n - 1, (BID[-1] if d > 0 else ASK[-1]), 'OPEN'

def exit_scan_be(j0, d, entry, sl, tp, be_at):
    """SL/TP, and once price reaches be_at the SL moves to entry"""
    if be_at is None: return exit_scan(j0, d, sl, tp)
    j = j0; n = len(TT)
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
    return n - 1, (BID[-1] if d > 0 else ASK[-1]), 'OPEN'

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
        if name not in P['LevelTFs'].split(','): continue   # aligning levels only from these TFs
        for side, v, kind in mms[name].levels():
            (sd if side == 'S' else rd).setdefault(v, set()).add((name, kind))
    ok = lambda mem: len({m[0] for m in mem}) >= P['MinTF'] and len({m[0] for m in mem if m[1] == 'Maj'}) >= P['MinMajor']
    ALS = [v for v, mem in sd.items() if ok(mem)]; ALR = [v for v, mem in rd.items() if ok(mem)]

def swap_cost(t0, t1):
    s = 0.0; d0 = int(t0 // 86400); d1 = int(t1 // 86400)
    for last_day in range(d0, d1):
        s += P['SwapLongPerLot'] * P['Lot'] * (3 if (P['SwapTriple'] and (last_day + 3) % 7 == 2) else 1)
    return s

EXITS = [('LEVEL', False), ('LVL>=1R', False), ('1:1', False), ('1:1.5', False), ('1:2', False), ('1:3', False)]
books = {(m, x): dict(busy=0.0, trades=[], fb=0) for m in P['Modes'].split(',') for x in EXITS}
signals = {'REV': 0, 'BO': 0}
# setup state (reset each new D1 session)
dk_cur = -1
rs = dict(tS=-1, tR=-1)     # reversal: first touch bar of the current touch run (support / resistance)
bo = dict(up=-1, dn=-1, usedU=False, usedD=False)
boA = dict(up=-1, dn=-1, usedU=False, usedD=False)     # breakout: bar of the close through the zone
last_below = -1; last_above = -1   # last bar that traded below the resistance zone / above the support zone

i0 = int(np.searchsorted(B['time'], start - 20 * 86400))
def do_sq(d, j0):
    for bk in books.values():
        lt_ = bk['trades'][-1] if bk['trades'] else None
        if lt_ is not None and TT[j0] < bk['busy'] and lt_['d'] == -d:
            px = BID[j0] if lt_['d'] > 0 else ASK[j0]
            pl = (px - lt_['entry']) * lt_['d'] * P['Lot'] * P['Contract'] - P['Comm'] * P['Lot'] * 2
            if lt_['d'] > 0: pl += swap_cost(lt_['t'], TT[j0])
            lt_.update(te=TT[j0], pl=pl, R=(px - lt_['entry']) * lt_['d'] / lt_['risk'], why='SQUARE-OFF')
            bk['busy'] = TT[j0]
for i in range(i0, len(B)):
    bt = B['time'][i]; T = bt + sec
    if T >= end: break
    r = cisd(i)
    rA = cisd_algo(i) if (P['ExitAlgo'] and P['CISD'] == 'lux') else r
    advance_levels(T)
    dk = int(np.searchsorted(d1t, bt, side='right') - 1)
    if dk < 0 or np.isnan(Z[dk, 0]): continue
    z1, z2, z3, z4 = Z[dk]; rLo, rHi = min(z1, z2), max(z1, z2); sLo, sHi = min(z3, z4), max(z3, z4)
    if dk != dk_cur:
        dk_cur = dk; rs['tS'] = rs['tR'] = -1
        for b__ in (bo, boA): b__['up'] = b__['dn'] = -1; b__['usedU'] = b__['usedD'] = False
    # --- update touches / breaks on this closed candle
    if H[i] >= rLo and rs['tR'] < 0: rs['tR'] = i
    if L[i] <= sHi and rs['tS'] < 0: rs['tS'] = i
    if C[i] > rHi: rs['tR'] = -1                      # closed above the resistance zone: no longer a reversal setup
    if C[i] < sLo: rs['tS'] = -1
    for b__ in (bo, boA):
        if C[i] <= rHi: b__['usedU'] = False
        if C[i] >= sLo: b__['usedD'] = False
        if C[i] > rHi and b__['up'] < 0 and not (P['ONE'] and b__['usedU']): b__['up'] = i
        if C[i] < sLo and b__['dn'] < 0 and not (P['ONE'] and b__['usedD']): b__['dn'] = i
        if b__['up'] >= 0 and C[i] < rHi: b__['up'] = -1
        if b__['dn'] >= 0 and C[i] > sLo: b__['dn'] = -1
    if L[i] < rLo: last_below = i
    if H[i] > sHi: last_above = i
    if T < start: continue
    sig = []
    # reversals: touch (this or an earlier candle today), CISD closing back outside the zone
    if r < 0 and rs['tR'] >= 0 and C[i] < rLo:
        sig.append(('REV', -1, (rHi + P['REVBuf']) if P['REVSL'] == 'zone' else H[rs['tR']:i+1].max() + P['SLBuf'], f"SELL from R zone {rLo:.2f}-{rHi:.2f}")); rs['tR'] = -1
    if r > 0 and rs['tS'] >= 0 and C[i] > sHi:
        sig.append(('REV', 1, (sLo - P['REVBuf']) if P['REVSL'] == 'zone' else L[rs['tS']:i+1].min() - P['SLBuf'], f"BUY from S zone {sLo:.2f}-{sHi:.2f}")); rs['tS'] = -1
    # breakouts: close beyond the zone, CISD (same or later candle) closing beyond it
    if (C[i] > rHi and C[i-1] <= rHi) if P['NOCISD'] else (r > 0 and bo['up'] >= 0 and C[i] > rHi):
        s0 = last_below if 0 <= last_below <= bo['up'] else bo['up']
        sig.append(('BO', 1, (rLo if P['BOSL'] == 'zone' else L[s0:i+1].min()) - P['SLBuf'], f"BREAKOUT of R zone {rLo:.2f}-{rHi:.2f}")); bo['up'] = -1; bo['usedU'] = True
    if (C[i] < sLo and C[i-1] >= sLo) if P['NOCISD'] else (r < 0 and bo['dn'] >= 0 and C[i] < sLo):
        s0 = last_above if 0 <= last_above <= bo['dn'] else bo['dn']
        sig.append(('BO', -1, (sHi if P['BOSL'] == 'zone' else H[s0:i+1].max()) + P['SLBuf'], f"BREAKDOWN of S zone {sLo:.2f}-{sHi:.2f}")); bo['dn'] = -1; bo['usedD'] = True
    if P['ExitAlgo'] and P['SQ']:
        sqd = []
        if rA > 0 and boA['up'] >= 0 and C[i] > rHi: sqd.append(1); boA['up'] = -1; boA['usedU'] = True
        if rA < 0 and boA['dn'] >= 0 and C[i] < sLo: sqd.append(-1); boA['dn'] = -1; boA['usedD'] = True
        jq = int(np.searchsorted(TT, T))
        if jq < len(TT):
            for dq in sqd: do_sq(dq, jq)
    if not sig: continue
    j0 = int(np.searchsorted(TT, T))
    if j0 >= len(TT): break
    for typ, d, sl, desc in sig:
        signals[typ] += 1
        entry = ASK[j0] if d > 0 else BID[j0]
        if P['SQ'] and not P['ExitAlgo']:
            for bk in books.values():
                lt_ = bk['trades'][-1] if bk['trades'] else None
                if lt_ is not None and TT[j0] < bk['busy'] and lt_['d'] == -d:
                    px = BID[j0] if lt_['d'] > 0 else ASK[j0]
                    pl = (px - lt_['entry']) * lt_['d'] * P['Lot'] * P['Contract'] - P['Comm'] * P['Lot'] * 2
                    if lt_['d'] > 0: pl += swap_cost(lt_['t'], TT[j0])
                    lt_.update(te=TT[j0], pl=pl, R=(px - lt_['entry']) * lt_['d'] / lt_['risk'], why='SQUARE-OFF')
                    bk['busy'] = TT[j0]
                    lg_ = bk['legs'][-1] if bk.get('legs') else None
                    if lg_ is not None and lg_['te'] > TT[j0] and lg_['d'] == -d:
                        px = BID[j0] if lg_['d'] > 0 else ASK[j0]
                        pl = (px - lg_['entry']) * lg_['d'] * P['Lot'] * P['Contract'] - P['Comm'] * P['Lot'] * 2
                        if lg_['d'] > 0: pl += swap_cost(lg_['t'], TT[j0])
                        lg_.update(te=TT[j0], pl=pl, R=(px - lg_['entry']) * lg_['d'] / lg_['risk'], why='SQUARE-OFF')
                        bk['legbusy'] = TT[j0]
        if QUIET(T): signals['quiet'] = signals.get('quiet', 0) + 1; continue   # v2.23 quiet market
        if P['TrendF']:
            tr_ = trend_at(T)
            if (P['TrendF'] == 1 and d != tr_) or (P['TrendF'] == 2 and d < 0 and tr_ > 0): signals['trend'] = signals.get('trend', 0) + 1; continue
        if P['DZNight'] and 90 <= ((int(T) + 19800) % 86400) // 60 < 330: signals['night'] = signals.get('night', 0) + 1; continue
        if P['FriNight'] and ((int(T) + 19800) // 86400 + 3) % 7 == 5 and (P['FriNight'] == 2 or ((int(T) + 19800) % 86400) // 60 < 330): signals['frinight'] = signals.get('frinight', 0) + 1; continue   # FriNight 1 = no DZ entries Sat 00:00-05:30 IST, 2 = all Saturday (IST)
        if P['Win'] and not any(int(w.split('-')[0]) <= ((int(T) + 19800) % 86400) // 60 < int(w.split('-')[1]) for w in P['Win'].split('/')): signals['win'] = signals.get('win', 0) + 1; continue   # Win = allowed IST entry windows, minutes 'a-b/c-d'
        if P['NoLondon'] and 750 <= ((int(T) + 19800) % 86400) // 60 < 1110: signals['london'] = signals.get('london', 0) + 1; continue   # no NEW entries 12:30-18:30 IST (square-off above still applies)
        if P['MaxRisk'] > 0 and typ == 'BO' and (entry - sl) * d > P['MaxRisk']: signals['skipped'] = signals.get('skipped', 0) + 1; continue
        capped = P['MaxSL'] > 0 and (entry - sl) * d > P['MaxSL']
        if capped: sl = entry - d * P['MaxSL']
        risk = (entry - sl) * d
        if risk <= 0: continue
        if P['MinRiskZ'] > 0 and risk < P['MinRiskZ'] * abs(z1 - z3) / 2: signals['weak'] = signals.get('weak', 0) + 1; continue
        if d > 0: lv = [v - P['TPBuf'] for v in ALR if v - P['TPBuf'] > entry]; lt = min(lv) if lv else None
        else:     lv = [v + P['TPBuf'] for v in ALS if v + P['TPBuf'] < entry]; lt = max(lv) if lv else None
        for (m, (xn, xbe)), bk in books.items():
            if m != 'BOTH' and m != typ: continue
            if TT[j0] < bk['busy']:
                ft = bk['trades'][-1] if bk['trades'] else None
                if P['DZLeg'] and ft is not None and ft['d'] == d and bk.get('legbusy', 0) <= TT[j0]                         and ((BID[j0] if d > 0 else ASK[j0]) - ft['entry']) * d > 0:
                    ltp = entry + d * P['DZLegTP'] * risk
                    je2, px2, why2 = exit_scan(j0, d, sl, ltp)
                    pl2 = (px2 - entry) * d * P['Lot'] * P['Contract'] - P['Comm'] * P['Lot'] * 2
                    if d > 0: pl2 += swap_cost(TT[j0], TT[je2])
                    bk.setdefault('legs', []).append(dict(t=TT[j0], te=TT[je2], typ='LEG', d=d, entry=entry, sl=sl, risk=risk, pl=pl2,
                                                          R=(px2 - entry) * d / risk, why=why2, desc='DZ LEG', tpR=P['DZLegTP']))
                    bk['legbusy'] = TT[je2]
                    # first trade -> breakeven: re-scan its exit from now with SL = entry, same TP
                    ftp = ft['entry'] + d * ft['tpR'] * ft['risk']
                    jf, pf, wf = exit_scan(j0, d, ft['entry'], ftp)
                    plf = (pf - ft['entry']) * d * P['Lot'] * P['Contract'] - P['Comm'] * P['Lot'] * 2
                    if d > 0: plf += swap_cost(ft['t'], TT[jf])
                    ft.update(te=TT[jf], pl=plf, R=(pf - ft['entry']) * d / ft['risk'], why=('BE-STOP' if wf == 'SL' else wf))
                    bk['busy'] = TT[jf]
                continue
            dkey = int((T + 19800) // 86400)
            if P['MaxDay'] > 0 and bk.setdefault('dc', {}).get(dkey, 0) >= P['MaxDay']: continue
            if xn.startswith('1:'): tp = entry + d * float(xn[2:]) * risk
            elif lt is not None and (xn == 'LEVEL' or (lt - entry) * d >= risk): tp = lt
            else: tp = entry + d * risk; bk['fb'] += 1        # no level (or under 1R for LVL>=1R): 1:1
            if P['MinTPPts'] > 0 and lt is not None and (lt - entry) * d < P['MinTPPts']: continue
            if capped and P['CapMinTPR'] > 0 and (tp - entry) * d < P['CapMinTPR'] * risk: continue
            je, px, why = exit_scan_be(j0, d, entry, sl, tp, entry + d * P['BE'] * risk if xbe else (entry + d * P['DZBER'] * risk if (P['DZBER'] > 0 and (tp - entry) * d > P['DZBER'] * risk) else None))
            pl = (px - entry) * d * P['Lot'] * P['Contract'] - P['Comm'] * P['Lot'] * 2
            if d > 0: pl += swap_cost(TT[j0], TT[je])
            bk['trades'].append(dict(t=TT[j0], te=TT[je], typ=typ, d=d, entry=entry, sl=sl, risk=risk, pl=pl,
                                     R=(px - entry) * d / risk, why=why, desc=desc, tpR=(tp - entry) * d / risk))
            bk['busy'] = TT[je]
            dc_ = bk.setdefault('dc', {}); dc_[dkey] = dc_.get(dkey, 0) + 1

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
    _j.dump({(xn if m == 'BO' else m + '|' + xn): [dict(t=float(x['t']), te=float(x['te']), pl=float(x['pl']), pts=float(x['R'] * x['risk']), risk=float(x['risk']), d=int(x['d']), why=x['why'], typ=x['typ']) for x in bk['trades'] + bk.get('legs', [])] for (m, (xn, xbe)), bk in books.items()}, open(P['TrOut'], 'w'))
