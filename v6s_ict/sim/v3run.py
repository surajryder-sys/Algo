import sys, numpy as np, datetime as dt
from v6sim import MajorMinor, agg, TFS

P = dict(Lot=0.06, Runner=0.01, SLBuf=0.5, TouchBuf=2.0, BER=2.0, PartR=3.0, Warm=3000, Tol=0.7, Contract=100.0, MinTF=2, Brake=0, M10AfterSL=0, MinMajor=0, TickSrc='real', MeqOut='meq.json', MaxHedges=0, DZF=0, MaxSL=0.0, SLFromTouch=0, OppTP=0, TPScope=0, TPBuf=1.0, AutoSq=0, BO=0, BOLots=0.05, BOBars=48, SwP=12, SwExp=100,
         SwapLongPerLot=-55.04, Start='2026-08-24', End='2026-09-25')
for a in sys.argv[1:]:
    k, v = a.split('='); P[k] = type(P[k])(v) if not isinstance(P[k], str) else v

IST = dt.timedelta(hours=5, minutes=30)
def ist(t): return (dt.datetime.fromtimestamp(int(t), dt.timezone.utc) + IST).strftime('%d %b %H:%M')

m1 = np.load('m1_2y.npy')
from ohlc import synth_ticks
REAL_FROM = int(np.datetime64('2026-04-01').astype('datetime64[s]').astype(np.int64))
if P['TickSrc'] == 'real':
    ticks = np.load('ticks.npy')
elif P['TickSrc'] == 'ohlc':
    ticks = synth_ticks(m1)
else:   # mixed: 1-minute-bar ticks before April 2026, real ticks after
    syn = synth_ticks(m1[m1['time'] < REAL_FROM]); real = np.load('ticks.npy')
    ticks = np.concatenate([syn, real[real[:, 0] >= REAL_FROM * 1000]])
start = int(np.datetime64(P['Start']).astype('datetime64[s]').astype(np.int64))
end = int(np.datetime64(P['End']).astype('datetime64[s]').astype(np.int64))
tk = ticks[(ticks[:, 0] >= start * 1000) & (ticks[:, 0] < end * 1000)]

bars = {name: agg(m1, sec) for name, sec in TFS}
mms = {name: MajorMinor(name) for name, _ in TFS}
ptr = {}
for name, sec in TFS:
    b = bars[name]; closed = np.flatnonzero(b['time'] + sec <= start)
    first = max(0, closed[-1] - P['Warm'] + 1)
    for k in range(first, closed[-1] + 1):
        mms[name].add(b['high'][k], b['low'][k], b['close'][k], int(b['time'][k]))
    ptr[name] = closed[-1] + 1

# M5 series for CISD / touches / SL (same bars as the M5 Major/Minor)
M5 = bars['M5']; O, H, L, C = [], [], [], []
bear, bull = [], []
shl, sll = [], []          # swing highs / lows, [0] = newest: (level, idx)
cswing = [None]            # swing behind the last confirmed CISD
def swing_step(i):
    sp = P['SwP']; c = i - sp
    if c >= 0 and c - sp >= 0:
        if all(H[k] < H[c] for k in range(c - sp, c + sp + 1) if k != c): shl.insert(0, (H[c], c))
        if all(L[k] > L[c] for k in range(c - sp, c + sp + 1) if k != c): sll.insert(0, (L[c], c))
    shl[:] = [(v, x) for v, x in shl if not (i - x >= P['SwExp'] or H[i] >= v)][:100]
    sll[:] = [(v, x) for v, x in sll if not (i - x >= P['SwExp'] or L[i] <= v)][:100]
def cisd_step(o, h, l, c):
    i = len(C); O.append(o); H.append(h); L.append(l); C.append(c)
    swing_step(i)
    if i >= 1:
        if C[i-1] < O[i-1] and c > o: bear.insert(0, (o, i))
        if C[i-1] > O[i-1] and c < o: bull.insert(0, (o, i))
    r = 0
    while bear:
        co, ci = bear[0]
        if C[i] < co:
            highest = max(0.0, max(C[ci:i+1])); top = 0.0; k = ci - 1
            while k >= 0 and C[k] < O[k]: top = O[k]; k -= 1
            d = top - co
            if d != 0 and (highest - co) / d > P['Tol']: bear.clear(); r = -1; break
            bear.pop(0)
        else: break
    while bull:
        co, ci = bull[0]
        if C[i] > co:
            lowest = min(C[ci:i+1]); bottom = 0.0; k = ci - 1
            while k >= 0 and C[k] > O[k]: bottom = O[k]; k -= 1
            d = co - bottom
            if d != 0 and (co - lowest) / d > P['Tol']: bull.clear(); r = 1; break
            bull.pop(0)
        else: break
    if r < 0: cswing[0] = shl[0][0] if shl else None
    if r > 0: cswing[0] = sll[0][0] if sll else None
    return r

class CisdTF:
    def __init__(s): s.O=[]; s.C=[]; s.bear=[]; s.bull=[]
    def step(s, o, c):
        O, C = s.O, s.C; i = len(C); O.append(o); C.append(c)
        if i >= 1:
            if C[i-1] < O[i-1] and c > o: s.bear.insert(0, (o, i))
            if C[i-1] > O[i-1] and c < o: s.bull.insert(0, (o, i))
        r = 0
        while s.bear:
            co, ci = s.bear[0]
            if C[i] < co:
                highest = max(0.0, max(C[ci:i+1])); top = 0.0; k = ci - 1
                while k >= 0 and C[k] < O[k]: top = O[k]; k -= 1
                d = top - co
                if d != 0 and (highest - co) / d > P['Tol']: s.bear.clear(); r = -1; break
                s.bear.pop(0)
            else: break
        while s.bull:
            co, ci = s.bull[0]
            if C[i] > co:
                lowest = min(C[ci:i+1]); bottom = 0.0; k = ci - 1
                while k >= 0 and C[k] > O[k]: bottom = O[k]; k -= 1
                d = co - bottom
                if d != 0 and (co - lowest) / d > P['Tol']: s.bull.clear(); r = 1; break
                s.bull.pop(0)
            else: break
        return r
M10 = bars['M10']; c10 = CisdTF()
m10closed = np.flatnonzero(M10['time'] + 600 <= start)
for k in range(max(0, m10closed[-1] - P['Warm'] + 1), m10closed[-1] + 1):
    c10.step(M10['open'][k], M10['close'][k])
m10ptr = m10closed[-1] + 1
needm10 = {}   # (side, level) -> True after that level's trade hit SL
m10_entries = 0

m5closed = np.flatnonzero(M5['time'] + 300 <= start)
for k in range(max(0, m5closed[-1] - P['Warm'] + 1), m5closed[-1] + 1):
    cisd_step(M5['open'][k], M5['high'][k], M5['low'][k], M5['close'][k])
m5ptr = m5closed[-1] + 1

sup = {}; res = {}   # value -> dict(touch=int, desc=str)
def rebuild():
    global sup, res
    s, r = {}, {}
    for name, _ in TFS:
        for side, v, kind in mms[name].levels():
            d = s if side == 'S' else r
            d.setdefault(v, set()).add((name, kind))
    ns = {}; nr = {}
    for v, mem in s.items():
        if len({m[0] for m in mem}) >= P['MinTF'] and len({m[0] for m in mem if m[1] == 'Maj'}) >= P['MinMajor']:
            ns[v] = dict(touch=sup.get(v, {}).get('touch', -1), est=sup.get(v, {}).get('est', -1), desc=', '.join(sorted(f"{a} {b}" for a, b in mem)))
    for v, mem in r.items():
        if len({m[0] for m in mem}) >= P['MinTF'] and len({m[0] for m in mem if m[1] == 'Maj'}) >= P['MinMajor']:
            nr[v] = dict(touch=res.get(v, {}).get('touch', -1), est=res.get(v, {}).get('est', -1), desc=', '.join(sorted(f"{a} {b}" for a, b in mem)))
    sup, res = ns, nr
rebuild()
bo = []


# ===================== V3.00 hedge-basket trading (appended to the v6run setup code) =====================
# Two independent baskets (+1 buys, -1 sells), each: a fresh trade managed like v2.00 but with a
# HEDGE stop instead of an SL; once hedged it is frozen until its own next qualifying setup, which
# drops the hedge (P/L booked to the basket ledger), adds one position and aims for +1R (<=2 hedges)
# or breakeven (3+ hedges) on ledger + open legs. No cap.
CONTRACT = P['Contract']

class Basket:
    def __init__(s, side):
        s.side = side; s.reset()
    def reset(s):
        s.legs = []            # [dir, vol, price, swap]
        s.state = 'idle'       # idle | fresh | hedged | recovery
        s.stop = None          # hedge stop price (pending) or BE exit price
        s.be = False; s.part = False; s.tp = None
        s.entry = s.risk = s.lvl = None; s.type = 0
        s.hcount = 0; s.ledger = 0.0; s.target = 0.0; s.t0 = None
        s.events = []
    def vol(s, d=None): return sum(l[1] for l in s.legs if d is None or l[0] == d)
    def floating(s, bid, ask):
        return sum(((bid - l[2]) if l[0] > 0 else (l[2] - ask)) * l[1] * CONTRACT + l[3] for l in s.legs)

bk = {1: Basket(1), -1: Basket(-1)}
D1 = agg(m1, 86400); d1t = D1['time']; rng = D1['high'] - D1['low']
Z2 = np.full(len(D1), np.nan); Z4 = np.full(len(D1), np.nan)
for k in range(10, len(D1)):
    h10 = rng[k-10:k].mean() / 2.0; Z2[k] = D1['open'][k] + h10; Z4[k] = D1['open'][k] - h10
dz_day = -1; dzblock = {1: False, -1: False}; dz_blocked = 0
realized = 0.0; eq_peak = 0.0; maxdd = 0.0; eq_min = 0.0; maxvol = 0.0; maxvol_t = 0
closed = []          # (side, open time, close time, pnl, hcount, reason)
stats = dict(fresh=0, hedges=0, drops=0, adds=0)
MEQ = {}; last_day = None; peak_t = 0; dd_info = None

def close_legs(b, legs, bid, ask):
    global realized
    pl = 0.0
    for l in legs:
        px = bid if l[0] > 0 else ask
        pl += (px - l[2]) * l[0] * l[1] * CONTRACT + l[3]
    realized += pl
    return pl

def close_basket(b, reason, bid, ask, now):
    pl = close_legs(b, b.legs, bid, ask)
    closed.append((b.side, b.t0, now, b.ledger + pl, b.hcount, reason))
    b.reset()

def open_leg(b, vol, bid, ask):
    px = ask if b.side > 0 else bid
    b.legs.append([b.side, vol, px, 0.0]); return px

def on_setup(b, sl, lvl, typ, bid, ask, now):
    lot = P['Lot'] if typ == 0 else P['BOLots']
    entry = ask if b.side > 0 else bid
    if P['MaxSL'] and abs(entry - sl) > P['MaxSL']: sl = entry - b.side * P['MaxSL']   # hedge level at most MaxSL away
    if (b.side > 0 and sl >= entry) or (b.side < 0 and sl <= entry): return
    if b.state == 'idle':
        open_leg(b, lot, bid, ask)
        b.state = 'fresh'; b.entry = entry; b.risk = abs(entry - sl); b.stop = sl; b.lvl = lvl; b.type = typ; b.t0 = now
        stats['fresh'] += 1
    elif b.state == 'hedged':
        # drop the hedge: close every opposite leg, book it to the ledger
        hed = [l for l in b.legs if l[0] != b.side]
        b.ledger += close_legs(b, hed, bid, ask)
        b.legs = [l for l in b.legs if l[0] == b.side]
        open_leg(b, lot, bid, ask)
        stats['drops'] += 1; stats['adds'] += 1
        b.state = 'recovery'; b.stop = sl
        r_money = abs(entry - sl) * lot * CONTRACT
        b.target = r_money if b.hcount <= 2 else 0.0
    # fresh / recovery: same-direction setup ignored (one add per dropped hedge)

def hedge_now(b, bid, ask, now):
    v = round(b.vol(b.side) - b.vol(-b.side), 2)
    if v <= 0: return
    px = bid if b.side > 0 else ask            # a sell stop fills at bid, a buy stop at ask
    b.legs.append([-b.side, v, px, 0.0])
    b.state = 'hedged'; b.hcount += 1; b.stop = None; b.tp = None
    stats['hedges'] += 1

for j in range(len(tk)):
    tms, bid, ask = tk[j]; now = tms / 1000.0
    day = int(now // 86400)
    if last_day is not None and day != last_day:
        mult = 3 if (last_day + 3) % 7 == 2 else 1
        for b in bk.values():
            for l in b.legs:
                if l[0] > 0: l[3] += P['SwapLongPerLot'] * l[1] * mult
    last_day = day

    while m5ptr < len(M5) and M5['time'][m5ptr] + 300 <= now:
        T = M5['time'][m5ptr] + 300
        changed = False
        for name, sec in TFS:
            b_ = bars[name]
            while ptr[name] < len(b_) and b_['time'][ptr[name]] + sec <= T:
                k = ptr[name]; mms[name].add(b_['high'][k], b_['low'][k], b_['close'][k], int(b_['time'][k])); ptr[name] += 1; changed = True
        if changed: rebuild()
        cs = cisd_step(M5['open'][m5ptr], M5['high'][m5ptr], M5['low'][m5ptr], M5['close'][m5ptr])
        bt = M5['time'][m5ptr]; i = len(C) - 1; m5ptr += 1
        if P['DZF']:
            dk = int(np.searchsorted(d1t, bt, side='right') - 1)
            if dk != dz_day: dz_day = dk; dzblock[1] = dzblock[-1] = False
            if dk >= 0 and not np.isnan(Z2[dk]):
                if C[i] > Z2[dk]: dzblock[-1] = True
                if C[i] < Z4[dk]: dzblock[1] = True
        last = m5ptr >= len(M5) or M5['time'][m5ptr] + 300 > now
        # auto square-off (fresh trades only)
        if last and cs != 0:
            b = bk[-cs]
            if b.state == 'fresh' and ((b.side > 0 and C[i] < b.lvl) or (b.side < 0 and C[i] > b.lvl)):
                close_basket(b, 'SQUARE-OFF', bid, ask, now)
        bo[:] = [x for x in bo if not (i - x[2] > P['BOBars'] or (C[i] < x[1] if x[0] > 0 else C[i] > x[1]))]
        if last and cs != 0 and P['DZF'] and dzblock[cs]:
            dz_blocked += 1
        elif last and cs != 0:
            side = cs; b = bk[side]
            # reversal setup for this side
            if side > 0:
                cand = [(d['touch'], v) for v, d in sup.items() if 0 <= d['touch'] < i and C[i] > v]
            else:
                cand = [(d['touch'], -v) for v, d in res.items() if 0 <= d['touch'] < i and C[i] < v]
            done = False
            if cand:
                t0, x = max(cand); lvl = x if side > 0 else -x
                sl = (min(L[t0+1:i+1]) - P['SLBuf']) if side > 0 else (max(H[t0+1:i+1]) + P['SLBuf'])
                on_setup(b, sl, lvl, 0, bid, ask, now)
                (sup if side > 0 else res)[lvl]['touch'] = -1
                done = True
            if not done:
                bc = [x for x in bo if x[0] == side and x[2] < i and (C[i] > x[1] if side > 0 else C[i] < x[1])]
                if bc:
                    s_, blv, bbar = max(bc, key=lambda x: x[2])
                    if cswing[0] is not None:
                        bsl = cswing[0] - P['SLBuf'] if side > 0 else cswing[0] + P['SLBuf']
                    else:
                        bsl = (min(L[bbar:i+1]) - P['SLBuf']) if side > 0 else (max(H[bbar:i+1]) + P['SLBuf'])
                    on_setup(b, bsl, blv, 1, bid, ask, now)
                    bo[:] = [x for x in bo if not (x[0] == side and x[1] == blv)]
        if i >= 1:
            for v in res:
                if C[i] > v and C[i-1] <= v and not any(x[0] == 1 and x[1] == v for x in bo): bo.append((1, v, i))
            for v in sup:
                if C[i] < v and C[i-1] >= v and not any(x[0] == -1 and x[1] == v for x in bo): bo.append((-1, v, i))
        if last:
            for b in bk.values():
                if b.state == 'fresh':
                    if b.side > 0:
                        cand = [r_ - P['TPBuf'] for r_ in res if r_ - P['TPBuf'] > bid]; b.tp = min(cand) if cand else None
                    else:
                        cand = [s_ + P['TPBuf'] for s_ in sup if s_ + P['TPBuf'] < ask]; b.tp = max(cand) if cand else None
        for v, d in sup.items():
            if L[i] <= v + P['TouchBuf']: d['touch'] = i
        for v, d in res.items():
            if H[i] >= v - P['TouchBuf']: d['touch'] = i

    # ---- per tick management
    for b in bk.values():
        if b.state == 'idle': continue
        px = bid if b.side > 0 else ask
        if b.state == 'fresh':
            if b.tp and ((b.side > 0 and bid >= b.tp) or (b.side < 0 and ask <= b.tp)):
                close_basket(b, 'TP', bid, ask, now); continue
            hit = (b.side > 0 and bid <= b.stop) or (b.side < 0 and ask >= b.stop)
            if hit:
                if b.be: close_basket(b, 'BE-STOP', bid, ask, now)
                else: hedge_now(b, bid, ask, now)
                continue
            g = (px - b.entry) * b.side
            if not b.be and g >= P['BER'] * b.risk: b.be = True; b.stop = b.entry
            if not b.part and g >= P['PartR'] * b.risk:
                b.part = True
                cv = round(b.vol(b.side) - P['Runner'], 2)
                if cv >= 0.01:
                    b.ledger += close_legs(b, [[b.side, cv, b.legs[0][2], 0.0]], bid, ask)
                    b.legs[0][1] = round(b.legs[0][1] - cv, 2)
        elif b.state == 'recovery':
            if b.ledger + b.floating(bid, ask) >= b.target:
                close_basket(b, 'RECOVERED' if b.target > 0 else 'RECOVERED-BE', bid, ask, now); continue
            if (b.side > 0 and bid <= b.stop) or (b.side < 0 and ask >= b.stop):
                if P['MaxHedges'] and b.hcount >= P['MaxHedges']:
                    close_basket(b, 'CAP-STOP', bid, ask, now)   # safety limit: take the loss instead of hedging again
                else:
                    hedge_now(b, bid, ask, now)
    tv = bk[1].vol() + bk[-1].vol()
    if tv > maxvol: maxvol = tv; maxvol_t = now
    if j % 50 == 0:
        eq = realized + bk[1].floating(bid, ask) + bk[-1].floating(bid, ask)
        if eq > eq_peak: eq_peak = eq; peak_t = now
        mk = (dt.datetime.fromtimestamp(int(now), dt.timezone.utc) + IST).strftime('%Y-%m')
        MEQ.setdefault(mk, [eq, eq, eq, 0.0])          # [first, last, min, peak-to-trough drop within month]
        me = MEQ[mk]; me[1] = eq; me[2] = min(me[2], eq); me[3] = max(me[3], max(me[0], eq_peak) - eq) if False else me[3]
        if eq_peak - eq > maxdd: maxdd = eq_peak - eq; dd_info = (peak_t, eq_peak, now, eq)
        eq_min = min(eq_min, eq)

bid, ask = tk[-1, 1], tk[-1, 2]
import json; json.dump(MEQ, open(P.get('MeqOut', 'meq.json'), 'w'))
open_note = []
for b in bk.values():
    if b.state != 'idle':
        open_note.append(f"{'BUY' if b.side > 0 else 'SELL'} basket still open: state {b.state}, hedges {b.hcount}, "
                         f"legs {b.vol(1):.2f} buy / {b.vol(-1):.2f} sell, ledger {b.ledger:+.0f}, floating {b.floating(bid, ask):+.0f}")
final_eq = realized + bk[1].floating(bid, ask) + bk[-1].floating(bid, ask)
print(f"DZ filter {P['DZF']}: setups blocked {dz_blocked}")
print(f"V3 hedge | {P['Start']} -> {P['End']} ticks {P['TickSrc']}")
print(f"equity result {final_eq:+.2f} (realized {realized:+.2f}) | worst drop {maxdd:.2f} | lowest {eq_min:+.2f} | max open lots {maxvol:.2f} on {ist(maxvol_t)} IST")
print(f"baskets closed {len(closed)} | fresh trades {stats['fresh']} | hedges {stats['hedges']} | hedge drops {stats['drops']}")
from collections import Counter
print('closed by:', dict(Counter(c[5] for c in closed)), '| by hedge count:', dict(Counter(c[4] for c in closed)))
if dd_info: print(f"WORST DROP: {ist(dd_info[0])} ({dd_info[1]:+.0f}) -> {ist(dd_info[2])} ({dd_info[3]:+.0f})")
for n in open_note: print(n)
worst = sorted(closed, key=lambda c: c[3])[:3]
print('worst baskets:', [(ist(c[1]), round(c[3]), c[4], c[5]) for c in worst])
mm = {}
for c in closed:
    k = (dt.datetime.fromtimestamp(int(c[2]), dt.timezone.utc) + IST).strftime('%Y-%m'); mm[k] = mm.get(k, 0) + c[3]
print('MONTHLY (closed baskets by close month):', ' '.join(f"{k}:{v:+.0f}" for k, v in sorted(mm.items())))
