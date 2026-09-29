import sys, numpy as np, datetime as dt
from v6sim import MajorMinor, agg, TFS

P = dict(Lot=0.06, Runner=0.01, SLBuf=0.5, TouchBuf=2.0, BER=2.0, PartR=3.0, Warm=3000, Tol=0.7, Contract=100.0, MinTF=2, Brake=0, M10AfterSL=0, MinMajor=0, TickSrc='real', MeqOut='meq.json', SLFromTouch=0, OppTP=0, TPScope=0, DZF=0, BOSL='swing', MaxSL=0.0, Comm=0.0, MinSpread=0.0, News=0, NewsMin=5, MinRR=0.0, TPNth=1, MinRRRev=-1.0, MinRRBO=-1.0, TrailR=0.0, TrailBars=0, FixTPR=0.0, SLMode='none', TPBuf=1.0, AutoSq=0, BO=0, BOLots=0.05, BOBars=48, SwP=12, SwExp=100,
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

bo = []; bo_trades = 0; sq_count = 0; rr_blocked = 0
def opp_tp(d, bid, ask):
    # TP at the Nth nearest opposite aligning level (buy: resistance - buffer above price; sell: support + buffer below)
    if d > 0: c = sorted(r_ - P['TPBuf'] for r_ in res if r_ - P['TPBuf'] > bid)
    else:     c = sorted((s_ + P['TPBuf'] for s_ in sup if s_ + P['TPBuf'] < ask), reverse=True)
    return c[P['TPNth'] - 1] if len(c) >= P['TPNth'] else None
def rr_ok(d, entry, sl, bid, ask, typ=0):
    need = P['MinRRBO'] if typ == 1 and P['MinRRBO'] >= 0 else P['MinRRRev'] if typ == 0 and P['MinRRRev'] >= 0 else P['MinRR']
    if not need: return True
    t = opp_tp(d, bid, ask)
    return t is None or abs(t - entry) >= need * abs(entry - sl)
# ---- approximate US news windows (server = UTC): 08:30 & 10:00 New York every weekday, 14:00 NY on FOMC days
def ny_offset_h(d):   # hours to add to NY local time to get UTC (EDT 4, EST 5)
    y = d.year
    mar = dt.date(y, 3, 1); dst_start = mar + dt.timedelta(days=(6 - mar.weekday()) % 7 + 7)      # 2nd Sunday of March
    nov = dt.date(y, 11, 1); dst_end = nov + dt.timedelta(days=(6 - nov.weekday()) % 7)            # 1st Sunday of November
    return 4 if dst_start <= d < dst_end else 5
FOMC = ['2024-11-07','2024-12-18','2025-01-29','2025-03-19','2025-05-07','2025-06-18','2025-07-30','2025-09-17','2025-10-29',
        '2025-12-10','2026-01-28','2026-03-18','2026-04-29','2026-06-17','2026-07-29','2026-09-16']
NEWS = []
d0 = dt.date(2024, 9, 1)
while d0 <= dt.date(2026, 10, 1):
    if d0.weekday() < 5:
        off = ny_offset_h(d0)
        for hh, mm_ in ((8, 30), (10, 0)) + (((14, 0),) if d0.isoformat() in FOMC else ()):
            NEWS.append(int(dt.datetime(d0.year, d0.month, d0.day, hh, mm_, tzinfo=dt.timezone.utc).timestamp()) + off * 3600)
    d0 += dt.timedelta(days=1)
NEWS = np.array(sorted(NEWS)); news_blocked = 0
def in_news(t):
    k = np.searchsorted(NEWS, t)
    for kk in (k - 1, k):
        if 0 <= kk < len(NEWS) and abs(t - NEWS[kk]) <= P['NewsMin'] * 60: return True
    return False
# Dynamic Zones from D1 built out of the same 1-minute data: Z2 = open + avg10/2, Z4 = open - avg10/2
D1 = agg(m1, 86400); d1t = D1['time']; rng = D1['high'] - D1['low']
Z2 = np.full(len(D1), np.nan); Z4 = np.full(len(D1), np.nan)
for k in range(10, len(D1)):
    h10 = rng[k-10:k].mean() / 2.0; Z2[k] = D1['open'][k] + h10; Z4[k] = D1['open'][k] - h10
dz_day = -1; dzblock = {1: False, -1: False}; dz_blocked = 0
pos = None; trades = []; realized = 0.0; eq_min = 0.0; eq_peak = 0.0; maxdd = 0.0
MEQ = {}; last_day = None; setups_ignored = 0; streak = {1: 0, -1: 0}; paused = {1: False, -1: False}; braked = 0; peak_t = 0; dd_info = None

def pos_pl(bid, ask):
    if pos is None: return 0.0
    px = bid if pos['dir'] > 0 else ask
    return (px - pos['entry']) * pos['dir'] * pos['vol'] * P['Contract'] + pos['swap']

def close_pos(reason, bid, ask, t, vol=None):
    global pos, realized
    px = bid if pos['dir'] > 0 else ask
    v = pos['vol'] if vol is None else vol
    pl = (px - pos['entry']) * pos['dir'] * v * P['Contract']
    swap_part = pos['swap'] * (v / pos['vol']); pos['swap'] -= swap_part
    cm = P['Comm'] * v * 2      # commission for this part, charged per side (open + close)
    realized += pl + swap_part - cm; pos['booked'] += pl + swap_part - cm
    pos['events'].append(f"{reason} {v:.2f}@{px:.2f} ({pl + swap_part:+.2f}) {ist(t)}")
    if vol is None or abs(pos['vol'] - v) < 1e-9:
        pos['exit'] = reason; trades.append(pos)
        d = pos['dir']
        if pos.get('type', 0) == 1:
            pos = None; return
        if P['M10AfterSL'] and reason == 'SL' and pos['booked'] < 0:
            needm10[(d, pos['lvl'])] = True
        if reason == 'SL' and pos['booked'] < 0:
            streak[d] += 1
            if P['Brake'] and streak[d] >= P['Brake'] and not paused[d]:
                paused[d] = True; pos['events'].append(f"BRAKE: {'buys' if d > 0 else 'sells'} paused")
        elif pos['booked'] > 0:
            streak[d] = 0
        pos = None
    else:
        pos['vol'] = round(pos['vol'] - v, 2)

for j in range(len(tk)):
    tms, bid, ask = tk[j]; now = tms / 1000.0
    if ask - bid < P['MinSpread']: ask = bid + P['MinSpread']
    day = int(now // 86400)
    if last_day is not None and day != last_day and pos is not None and pos['dir'] > 0:
        mult = 3 if (last_day + 3) % 7 == 2 else 1
        pos['swap'] += P['SwapLongPerLot'] * pos['vol'] * mult
    last_day = day

    # ---- closed M5 bar(s) -> levels, CISD, signal, touches
    fired = False
    while m5ptr < len(M5) and M5['time'][m5ptr] + 300 <= now:
        T = M5['time'][m5ptr] + 300
        changed = False
        for name, sec in TFS:
            b = bars[name]
            while ptr[name] < len(b) and b['time'][ptr[name]] + sec <= T:
                k = ptr[name]; mms[name].add(b['high'][k], b['low'][k], b['close'][k], int(b['time'][k])); ptr[name] += 1; changed = True
        if changed: rebuild()
        cs = cisd_step(M5['open'][m5ptr], M5['high'][m5ptr], M5['low'][m5ptr], M5['close'][m5ptr])
        i = len(C) - 1; bt = M5['time'][m5ptr]; m5ptr += 1
        if P['DZF']:
            dk = int(np.searchsorted(d1t, bt, side='right') - 1)
            if dk != dz_day: dz_day = dk; dzblock[1] = dzblock[-1] = False      # new session: filter resets
            if dk >= 0 and not np.isnan(Z2[dk]):
                if C[i] > Z2[dk]: dzblock[-1] = True     # closed above the upper zone line -> no sells today
                if C[i] < Z4[dk]: dzblock[1] = True      # closed below the lower zone line -> no buys today
        cs10 = 0; c10close = None
        while m10ptr < len(M10) and M10['time'][m10ptr] + 600 <= T:
            cs10 = c10.step(M10['open'][m10ptr], M10['close'][m10ptr]); c10close = M10['close'][m10ptr]; m10ptr += 1
        last = m5ptr >= len(M5) or M5['time'][m5ptr] + 300 > now
        # C. auto square-off: opposite CISD closing beyond the trade's level
        if last and P['AutoSq'] and pos is not None and cs == -pos['dir']:
            if (pos['dir'] > 0 and C[i] < pos['lvl']) or (pos['dir'] < 0 and C[i] > pos['lvl']):
                close_pos('SQUARE-OFF', bid, ask, now); sq_count += 1
        # B. breakout candidates: expire / cancel on a close back through
        bo[:] = [b_ for b_ in bo if not (i - b_[2] > P['BOBars'] or (C[i] < b_[1] if b_[0] > 0 else C[i] > b_[1]))]
        sig = 0
        if last and (cs != 0 or cs10 != 0):
            def conf(side, v):
                # which confirmation this level needs: M10 after its own SL, M5 otherwise
                if P['M10AfterSL'] and needm10.get((side, v)):
                    ok = cs10 == side and c10close is not None and (c10close > v if side > 0 else c10close < v)
                    return ok, 'M10'
                ok = cs == side and (C[i] > v if side > 0 else C[i] < v)
                return ok, 'M5'
            bc = [(d['touch'], v) for v, d in sup.items() if 0 <= d['touch'] < i and conf(1, v)[0]]
            sc = [(d['touch'], -v) for v, d in res.items() if 0 <= d['touch'] < i and conf(-1, v)[0]]
            if bc and (not sc or max(bc)[0] >= max(sc)[0]):
                t0, lvl = max(bc); s0 = sup[lvl]['est'] if P['SLFromTouch'] else t0 + 1
                sl = min(L[s0:i+1]) - P['SLBuf']; sig = 1; desc = sup[lvl]['desc']; how = conf(1, lvl)[1]
            elif sc:
                t0, nl = max(sc); lvl = -nl; s0 = res[lvl]['est'] if P['SLFromTouch'] else t0 + 1
                sl = max(H[s0:i+1]) + P['SLBuf']; sig = -1; desc = res[lvl]['desc']; how = conf(-1, lvl)[1]
            if sig:
                if pos is not None and pos['dir'] == sig:
                    setups_ignored += 1
                elif dzblock[sig]:
                    dz_blocked += 1
                elif P['News'] and in_news(now):
                    news_blocked += 1
                elif paused[sig]:
                    braked += 1
                else:
                    if pos is not None: close_pos('REVERSE', bid, ask, now)
                    entry = ask if sig > 0 else bid
                    if P['SLMode'] == 'cap' and abs(entry - sl) > P['MaxSL']: sl = entry - sig * P['MaxSL']
                    if P['SLMode'] == 'skip' and abs(entry - sl) > P['MaxSL']: sl = entry + sig   # invalid -> skipped
                    if not rr_ok(sig, entry, sl, bid, ask): rr_blocked += 1; sl = entry + sig
                    if (sig > 0 and sl < entry) or (sig < 0 and sl > entry):
                        if paused[-sig] or streak[-sig]:
                            paused[-sig] = False; streak[-sig] = 0   # the other side traded -> lift its brake
                        pos = dict(dir=sig, entry=entry, sl0=sl, sl=sl, vol=P['Lot'], part=False, swap=0.0, booked=0.0,
                                   t=now, lvl=lvl, desc=desc + (' | M10 CISD' if how == 'M10' else ''), risk=abs(entry - sl), events=[])
                        if how == 'M10': m10_entries += 1
                        needm10.pop((sig, lvl), None)
                    (sup if sig > 0 else res)[lvl]['touch'] = -1
        # B. breakout entry (only when no reversal setup fired this candle)
        if last and P['BO'] and sig == 0 and cs != 0:
            cands = [b_ for b_ in bo if b_[0] == cs and b_[2] < i and (C[i] > b_[1] if cs > 0 else C[i] < b_[1])]
            if cands:
                side, blv, bbar = max(cands, key=lambda x: x[2])
                if cswing[0] is not None and P['BOSL'] == 'swing':
                    bsl = cswing[0] - P['SLBuf'] if cs > 0 else cswing[0] + P['SLBuf']
                else:
                    bsl = (min(L[bbar:i+1]) - P['SLBuf']) if cs > 0 else (max(H[bbar:i+1]) + P['SLBuf'])
                if pos is not None and pos['dir'] == cs:
                    setups_ignored += 1
                elif dzblock[cs]:
                    dz_blocked += 1
                elif P['News'] and in_news(now):
                    news_blocked += 1
                else:
                    if pos is not None: close_pos('REVERSE', bid, ask, now)
                    entry = ask if cs > 0 else bid
                    if P['SLMode'] == 'cap' and abs(entry - bsl) > P['MaxSL']: bsl = entry - cs * P['MaxSL']
                    if P['SLMode'] == 'skip' and abs(entry - bsl) > P['MaxSL']: bsl = entry + cs
                    if not rr_ok(cs, entry, bsl, bid, ask, 1): rr_blocked += 1; bsl = entry + cs
                    if (cs > 0 and bsl < entry) or (cs < 0 and bsl > entry):
                        pos = dict(dir=cs, entry=entry, sl0=bsl, sl=bsl, vol=P['BOLots'], part=False, swap=0.0, booked=0.0,
                                   t=now, lvl=blv, desc=('BREAKOUT' if cs > 0 else 'BREAKDOWN'), risk=abs(entry - bsl), events=[], type=1)
                        bo_trades += 1
                    bo[:] = [b_ for b_ in bo if b_ is not None and not (b_[0] == side and b_[1] == blv)]
        # B. register new breaks by this candle
        if P['BO'] and i >= 1:
            for v in res:
                if C[i] > v and C[i-1] <= v and not any(b_[0] == 1 and b_[1] == v for b_ in bo): bo.append((1, v, i))
            for v in sup:
                if C[i] < v and C[i-1] >= v and not any(b_[0] == -1 and b_[1] == v for b_ in bo): bo.append((-1, v, i))
        # trailing by the last N closed M5 candles (only after breakeven)
        if last and P['TrailBars'] and pos is not None and pos['sl'] != pos['sl0'] and (pos['dir'] > 0 and pos['sl'] >= pos['entry'] or pos['dir'] < 0 and pos['sl'] <= pos['entry']):
            n_ = P['TrailBars']
            if pos['dir'] > 0: pos['sl'] = max(pos['sl'], min(L[i-n_+1:i+1]) - P['SLBuf'])
            else:              pos['sl'] = min(pos['sl'], max(H[i-n_+1:i+1]) + P['SLBuf'])
        # A. TP at the nearest opposite aligning level
        if last and P['OppTP'] and pos is not None and (P['TPScope'] == 0 or pos.get('type', 0) == 1):
            pos['tp'] = opp_tp(pos['dir'], bid, ask)
        for v, d in sup.items():
            if L[i] <= v + P['TouchBuf']:
                if d['touch'] != i - 1: d['est'] = i      # a new run of touching candles starts here
                d['touch'] = i
        for v, d in res.items():
            if H[i] >= v - P['TouchBuf']:
                if d['touch'] != i - 1: d['est'] = i
                d['touch'] = i

    # ---- position management on this tick
    if pos is not None and pos.get('tp') and ((pos['dir'] > 0 and bid >= pos['tp']) or (pos['dir'] < 0 and ask <= pos['tp'])):
        close_pos('TP', bid, ask, now)
    if pos is not None and P['FixTPR'] and ((bid if pos['dir'] > 0 else ask) - pos['entry']) * pos['dir'] >= P['FixTPR'] * pos['risk']:
        close_pos('FIX-TP', bid, ask, now)
    if pos is not None:
        px = bid if pos['dir'] > 0 else ask
        if (pos['dir'] > 0 and bid <= pos['sl']) or (pos['dir'] < 0 and ask >= pos['sl']):
            close_pos('BE-STOP' if pos['sl'] == pos['entry'] else ('TRAIL' if pos.get('be') else 'SL'), bid, ask, now)
        else:
            gained = (px - pos['entry']) * pos['dir']
            if not pos.get('be') and gained >= P['BER'] * pos['risk']:
                pos['sl'] = pos['entry']; pos['be'] = True; pos['events'].append(f"BE {ist(now)}")
            if P['TrailR'] and pos.get('be'):
                t_ = px - pos['dir'] * P['TrailR'] * pos['risk']
                if (pos['dir'] > 0 and t_ > pos['sl']) or (pos['dir'] < 0 and t_ < pos['sl']): pos['sl'] = t_
            if not pos['part'] and gained >= P['PartR'] * pos['risk']:
                pos['part'] = True
                cv = round(pos['vol'] - P['Runner'], 2)
                if cv >= 0.01: close_pos('PARTIAL', bid, ask, now, cv)
    if j % 50 == 0:
        eq = realized + pos_pl(bid, ask)
        if eq > eq_peak: eq_peak = eq; peak_t = now
        mk = (dt.datetime.fromtimestamp(int(now), dt.timezone.utc) + IST).strftime('%Y-%m')
        MEQ.setdefault(mk, [eq, eq, eq, 0.0])          # [first, last, min, peak-to-trough drop within month]
        me = MEQ[mk]; me[1] = eq; me[2] = min(me[2], eq); me[3] = max(me[3], max(me[0], eq_peak) - eq) if False else me[3]
        if eq_peak - eq > maxdd: maxdd = eq_peak - eq; dd_info = (peak_t, eq_peak, now, eq)
        eq_min = min(eq_min, eq)

bid, ask = tk[-1, 1], tk[-1, 2]
import json; json.dump(MEQ, open(P.get('MeqOut', 'meq.json'), 'w'))
open_note = ''
if pos is not None:
    open_note = f"open at end: {'BUY' if pos['dir']>0 else 'SELL'} from {ist(pos['t'])}, floating {pos_pl(bid, ask):+.2f}"
    close_pos('END', bid, ask, tk[-1, 0] / 1000)

print(f"ticks: {P['TickSrc']}")
print(f"period {P['Start']} -> {P['End']}  lot {P['Lot']}  touch buffer {P['TouchBuf']}  min aligned TFs {P['MinTF']}")
tdays = len({int((t['t']) // 86400) for t in trades})
alld = len(np.unique((tk[:, 0] // 86400000).astype(np.int64)))
print(f"TRADES PER DAY: {len(trades) / max(1, alld):.2f} per trading day ({len(trades)} trades over {alld} trading days; trades on {tdays} of them)")
print(f"RR filter min {P['MinRR']} TP level #{P['TPNth']}: setups skipped {rr_blocked}")
print(f"NEWS filter {P['News']} (+/-{P['NewsMin']} min): setups blocked {news_blocked}")
print(f"DZ filter {P['DZF']}: setups blocked {dz_blocked}")
print(f"v2: oppTP {P['OppTP']} autoSq {P['AutoSq']} ({sq_count} square-offs) breakouts {P['BO']} ({bo_trades} breakout trades)")
print(f"min major TFs {P['MinMajor']} | brake {P['Brake']}: setups skipped by the brake {braked} | M10-after-SL {P['M10AfterSL']}: entries confirmed on M10 {m10_entries}")
print(f"trades {len(trades)}  net P/L {realized:+.2f}  worst drop {maxdd:.2f}  lowest point {eq_min:+.2f}  same-direction setups ignored {setups_ignored}")
wins = [t for t in trades if t['booked'] > 0]; losses = [t for t in trades if t['booked'] <= 0]
print(f"winners {len(wins)} (sum {sum(t['booked'] for t in wins):+.2f})  losers/flat {len(losses)} (sum {sum(t['booked'] for t in losses):+.2f})")
from collections import Counter
print('exits:', dict(Counter(t['exit'] for t in trades)), '| partials hit:', sum(1 for t in trades if t['part']))
if open_note: print(open_note)
if dd_info: print(f"WORST DROP: peak {dd_info[1]:+.2f} on {ist(dd_info[0])} IST -> low {dd_info[3]:+.2f} on {ist(dd_info[2])} IST = -{dd_info[1]-dd_info[3]:.2f}")
print()
for t in trades:
    print(f"{ist(t['t'])} {'BUY ' if t['dir']>0 else 'SELL'} @{t['entry']:.2f} SL {t['sl0']:.2f} risk {t['risk']:.2f} lvl {t['lvl']:.3f} [{t['desc']}] -> {t['exit']:8s} {t['booked']:+8.2f} | {'; '.join(t['events'])}")

print()
print('MONTHLY (by trade entry, IST):')
from collections import defaultdict
mm = defaultdict(lambda: [0, 0.0, 0])
for t in trades:
    k = (dt.datetime.fromtimestamp(int(t['t']), dt.timezone.utc) + IST).strftime('%Y-%m')
    mm[k][0] += 1; mm[k][1] += t['booked']; mm[k][2] += t['booked'] > 0
for k in sorted(mm):
    n, s, w = mm[k]; print(f"  {k}: trades {n:3d}  win {w/n*100:4.0f}%  net {s:+9.2f}")
