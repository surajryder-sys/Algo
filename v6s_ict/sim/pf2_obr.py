"""Portfolio grid for the OB-retest strategy (s_obr.py Mgmt=1 MaxSeq>1 dump).
    python pf2_obr.py obr3.json
Base signals: OB TFs H2,H1, M5 LuxAlgo CISD, SL cap 20, OB height < 20 pts and < 0.25 x 10-day avg daily range, no Friday.
Options (each row of the grid changes one or more):
  Pos   max open positions (1 / 2 / 3 / 99); PerTF = at most one open per OB timeframe
  Re    trades allowed per OB (1 = first CISD only; 2-3 = later CISDs on the same still-valid OB after the previous closed)
  M     exit: T2 (2R), TA2 (aligning level if beyond 2R), T2_RUN (half at 2R, rest trails), T2_TS4 (4 h time stop), T3
  Size  lot = fixed 0.1 lot; risk = fixed $ risk per trade (same average $ risk as the fixed lot)
  Brake N = after N losses in a row no new entries until the next IST day
  MCut  X = once the month is down X $, half size for the rest of the month
  Conf  1.5 = size x1.5 when the OB overlaps another TF's OB or an aligning level sits in/within 2 of it
  OTF   OB timeframes (default H2,H1)"""
import sys, json, numpy as np, datetime as dt

T = json.load(open(sys.argv[1]))['trades']; U = dt.timezone.utc; SPL = 1761955200; SP0 = 1730419200   # 2025-11-01 / 2024-11-01
DATA = sys.argv[2] if len(sys.argv) > 2 else 'real7'
ist = lambda t: dt.datetime.fromtimestamp(t + 19800, U)
m1 = np.load(f'm1_{DATA}_tv.npy'); MT = m1['time'].astype(np.int64)
b = (MT // 86400) * 86400; idx = np.flatnonzero(np.diff(b)) + 1; st = np.concatenate([[0], idx])
dr = np.maximum.reduceat(m1['high'], st) - np.minimum.reduceat(m1['low'], st)
DT = b[st] + 86400; DA = np.convolve(dr, np.ones(10) / 10)[:len(dr)]
def adr(t):
    q = int(np.searchsorted(DT, t, side='right')) - 1; return DA[q] if q >= 10 else np.nan
LOT = 0.1
for r in T: r['adr'] = adr(r['rt']); r['hd'] = r['obsz'] / r['adr']
BASE = [r for r in T if r['ltf'] == 'M5' and r['ct'] == 'lux' and ist(r['t']).weekday() != 4]
AVG_RISK = np.mean([min(r['raw'], 20) for r in BASE if r['seq'] == 0 and r['otf'] in ('H2', 'H1')])

def run(OTF='H2,H1', Pos=1, PerTF=0, Re=1, M='T2', Size='lot', Brake=0, MCut=0, Conf=1.0, QADR=0, HDMax=0.25, HTMax=20, MinStop=0):
    S = sorted([r for r in BASE if r['otf'] in OTF.split(',') and r['seq'] < 8 and r['adr'] >= QADR and r['hd'] < HDMax and r['obsz'] < HTMax and r['raw'] >= MinStop], key=lambda r: (r['t'], r['seq']))
    open_ = []; tr = []; per_ob = {}; ob_busy = {}; streak = 0; brake_day = None; mon_pl = {}
    for r in S:
        t = r['t']; open_ = [o for o in open_ if o['x'] > t]
        if Brake:   # N losses in a row among trades already closed -> no new entries for the rest of that IST day
            cl = sorted([x for x in tr if x['x'] <= t], key=lambda x: x['x'])[-Brake:]
            if len(cl) == Brake and all(x['R'] < 0 for x in cl) and (cl[-1]['x'] + 19800) // 86400 == (t + 19800) // 86400: continue
        if len(open_) >= Pos: continue
        if PerTF and any(o['otf'] == r['otf'] for o in open_): continue
        if Re == 1 and r['seq'] > 0: continue      # 1 per OB = the first CISD after the retest only
        if per_ob.get(r['obid'], 0) >= Re or ob_busy.get(r['obid'], 0) > t: continue
        R_, xt = r['m20'][M]; risk = min(r['raw'], 20)
        size = 1.0
        if Conf != 1.0 and (r['ovl'] >= 1 or r['lvlz'] <= 2): size *= Conf
        mk = ist(t).strftime('%y-%m')
        if MCut and sum(x['usd'] for x in tr if x['x'] <= t and ist(x['x']).strftime('%y-%m') == mk) <= -MCut: size *= 0.5
        usd = R_ * (risk * LOT * 100 if Size == 'lot' else AVG_RISK * LOT * 100) * size
        x = dict(t=t, x=xt, R=R_, usd=usd, otf=r['otf']); tr.append(x); open_.append(x)
        per_ob[r['obid']] = per_ob.get(r['obid'], 0) + 1; ob_busy[r['obid']] = xt
    if not tr: return None
    ev = sorted([(x['x'], x['usd']) for x in tr]); eq = pk = dd = 0.0; mon = {}
    for xt, u in ev:
        eq += u; pk = max(pk, eq); dd = max(dd, pk - eq); m = ist(xt).strftime('%y-%m'); mon[m] = mon.get(m, 0) + u
    mon.pop('26-10', None)   # partial month (2 days)
    p0 = sum(x['usd'] for x in tr if x['t'] < SP0); a = sum(x['usd'] for x in tr if SP0 <= x['t'] < SPL); bb = sum(x['usd'] for x in tr if x['t'] >= SPL)
    lm0 = sum(v < 0 for k, v in mon.items() if k < '24-11')
    return dict(n=len(tr), win=np.mean([x['R'] > 0 for x in tr]) * 100, R=np.mean([x['R'] for x in tr]), net=eq, dd=dd,
                lm=sum(v < 0 for v in mon.values()), nm=len(mon), worst=min(mon.values()), y1=a, y2=bb, p0=p0, lm0=lm0, mon=mon)

def show(lab, **kw):
    s = run(**kw)
    if s is None: print(f"{lab:44s} no trades"); return
    print(f"{lab:44s} {s['n']:4d} {s['win']:4.0f}% {s['R']:+.2f} {s['net']:+8,.0f} {s['dd']:6,.0f} {s['net']/s['dd']:5.1f} "
          f"{s['lm']:2d}/{s['nm']} {s['worst']:+6,.0f} {s['p0']:+7,.0f} ({s['lm0']}) {s['y1']:+7,.0f} {s['y2']:+7,.0f}")
    return s
print(f"lot {LOT}, average stop {AVG_RISK:.1f} pts (fixed-risk = ${AVG_RISK * LOT * 100:.0f} per trade)")
print(f"{'variant':44s} {'n':>4s} {'win':>5s} {'R/tr':>5s} {'net $':>8s} {'maxDD':>6s} {'n/DD':>5s} {'lm':>5s} {'worstM':>6s} {'Jan-Oct24':>11s} {'Nov24-Oct25':>7s} {'Nov25-':>7s}")
G = [('BASE (as test5.0)', {}),
     ('-- minimum OB stop (entry to OB edge + 0.5)', None),
     ('stop >= 3', dict(MinStop=3)), ('stop >= 4', dict(MinStop=4)), ('stop >= 5', dict(MinStop=5)),
     ('stop >= 6', dict(MinStop=6)), ('stop >= 7', dict(MinStop=7)),
     ('-- combos', None), ('stop >= 5 + ADR >= 20', dict(MinStop=5, QADR=20)),
     ('stop >= 5 + 1 per OB TF', dict(MinStop=5, Pos=99, PerTF=1)),
     ('stop >= 5, H4,H2,H1', dict(MinStop=5, OTF='H4,H2,H1')),
     ('stop >= 5 + height < 0.30 x ADR', dict(MinStop=5, HDMax=0.30)),
     ('stop >= 5 + height < 25 pts', dict(MinStop=5, HTMax=25))]
for lab, kw in G:
    if kw is None: print(lab); continue
    show(lab, **kw)
