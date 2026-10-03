"""Management / filter / portfolio analysis of an s_obr.py Mgmt=1 dump.
    python pf_obr.py obr2.json [OTF=H2,H1] [LTF=M5] [CT=lux] [Cap=20] [M=T3] [F=h4,brk,loc,ht15,nofri] [Lot=0.04]
  M = management key: T2 / T3 (plain), T2_BE1 / T3_BE1.5 (breakeven at x R), T2_P1 / T3_P1 (half off at 1R + BE)
  F = filters: h4 / h1 / m15 (that CISD agrees), brk (no DZ breakout against), loc (not beyond the far zone in the trade
      direction), ht15 (OB height < 15), nofri, nolate (no 00:00-05:30 IST entries)
  Portfolio: one position at a time, signals in time order; P/L $ = R x risk x Lot x 100."""
import sys, json, numpy as np, datetime as dt

A = dict(Out='', OTF='H2,H1', LTF='M5', CT='lux', Cap='20', M='T3', F='', Lot='0.04', Grid='1')
for a in sys.argv[2:]:
    k, v = a.split('='); A[k] = v
T = json.load(open(sys.argv[1]))['trades']; U = dt.timezone.utc; SPL = 1761955200
ist = lambda t: dt.datetime.fromtimestamp(t + 19800, U)
FL = [f for f in A['F'].split(',') if f]
# volatility at the retest: ATR(14) of the OB's own TF and of H1 (closed bars), 10-day average daily range
m1 = np.load('m1_real7_tv.npy'); MT = m1['time'].astype(np.int64)
def agg(sec):
    b = (MT // sec) * sec; idx = np.flatnonzero(np.diff(b)) + 1
    st = np.concatenate([[0], idx]); en = np.concatenate([idx, [len(b)]])
    return b[st], np.maximum.reduceat(m1['high'], st), np.minimum.reduceat(m1['low'], st), m1['close'][en - 1]
VOL = {}
for nm, sec, n in (('H4', 14400, 14), ('H2', 7200, 14), ('H1', 3600, 14), ('M30', 1800, 14), ('D1', 86400, 10)):
    t, h, l, c = agg(sec); pc = np.concatenate([[c[0]], c[:-1]])
    tr = (h - l) if nm == 'D1' else (np.maximum(h, pc) - np.minimum(l, pc))
    a = np.convolve(tr, np.ones(n) / n)[:len(tr)]; VOL[nm] = (t + sec, a)
def vol(nm, t):
    ct, a = VOL[nm]; q = int(np.searchsorted(ct, t, side='right')) - 1; return float(a[q]) if q >= 14 else np.nan
for r in T:
    r['ha'] = r['obsz'] / vol(r['otf'], r['rt']); r['hh'] = r['obsz'] / vol('H1', r['rt']); r['hd'] = r['obsz'] / vol('D1', r['rt'])
def ok(r):
    for f in FL:
        if f in ('h4', 'h1', 'm15') and r[f] != 1: return False
        if f == 'brk' and r['brk'] < 0: return False
        if f == 'loc' and r['loc'] <= -2: return False
        if f[:2] in ('ha', 'hh', 'hd') and not (r[f[:2]] < float(f[2:])): return False
        if f.startswith('ht') and r['obsz'] >= float(f[2:]): return False
        if f.startswith('rk') and r['raw'] >= float(f[2:]): return False
        if f == 'nofri' and ist(r['t']).weekday() == 4: return False
        if f == 'nolate' and ist(r['t']).hour < 5 or (f == 'nolate' and ist(r['t']).hour == 5 and ist(r['t']).minute < 30): return False
    return True
sel = lambda otf, ltf, ct: [r for r in T if r['otf'] in otf.split(',') and r['ltf'] in ltf.split(',') and r['ct'] in ct.split(',') and ok(r)]
cap = A['Cap']
def stat(L, key):
    v = [(r['t'], r['m' + cap][key][0]) for r in L]
    if not v: return '      -       '
    a = [x for t, x in v if t < SPL]; b = [x for t, x in v if t >= SPL]
    return f"{np.mean(a) if a else 0:+.2f}/{np.mean(b) if b else 0:+.2f}{'*' if a and b and np.mean(a) > 0 and np.mean(b) > 0 else ' '}"
KEYS = ['T2', 'T2_BE1', 'T2_BE1.5', 'T2_P1', 'T3', 'T3_BE1', 'T3_BE1.5', 'T3_P1']
if A['Grid'] == '1':
    print(f"R/trade Y1/Y2 by management (cap {cap}, filters {FL or 'none'})  * = positive both years")
    print(f"{'OB':4s}{'LTF':4s}{'cisd':5s}{'n':>5s} " + ' '.join(f'{k:>14s}' for k in KEYS))
    for otf in ('H4', 'H2', 'H1', 'M30'):
        for ltf in ('M3', 'M5'):
            for ct in ('lux', 'aa'):
                L = sel(otf, ltf, ct); print(f"{otf:4s}{ltf:4s}{ct:5s}{len(L):5d} " + ' '.join(f'{stat(L, k):>14s}' for k in KEYS))
# ---------- portfolio ----------
L = sorted(sel(A['OTF'], A['LTF'], A['CT']), key=lambda r: r['t']); M = A['M']; lot = float(A['Lot'])
busy = 0; tr = []
for r in L:
    if r['t'] < busy: continue
    R_, xt = r['m' + cap][M]; risk = min(r['raw'], float(cap))
    tr.append(dict(t=r['t'], x=xt, R=R_, usd=R_ * risk * lot * 100, otf=r['otf'], d=r['d'])); busy = xt
if not tr: sys.exit('no trades')
eq = pk = dd = 0.0; mon = {}; wk = {}
for x in tr:
    eq += x['usd']; pk = max(pk, eq); dd = max(dd, pk - eq)
    mon[ist(x['x']).strftime('%y-%m')] = mon.get(ist(x['x']).strftime('%y-%m'), 0) + x['usd']
    wk[ist(x['x']).strftime('%G-%V')] = wk.get(ist(x['x']).strftime('%G-%V'), 0) + x['usd']
a = [x for x in tr if x['t'] < SPL]; b = [x for x in tr if x['t'] >= SPL]
print(f"\nPORTFOLIO one-at-a-time | OB {A['OTF']} | CISD {A['LTF']} {A['CT']} | cap {cap} | {M} | filters {FL or 'none'} | lot {lot}")
print(f"  trades {len(tr)} ({len(tr)/23:.1f}/month)  win {np.mean([x['R'] > 0 for x in tr])*100:.0f}%  R/tr {np.mean([x['R'] for x in tr]):+.3f}  "
      f"net ${eq:+,.0f}  max drop ${dd:,.0f}  losing months {sum(v < 0 for v in mon.values())}/{len(mon)}  losing weeks {sum(v < 0 for v in wk.values())}/{len(wk)}")
print(f"  Y1 {len(a)} trades ${sum(x['usd'] for x in a):+,.0f} R/tr {np.mean([x['R'] for x in a]) if a else 0:+.3f} | "
      f"Y2 {len(b)} trades ${sum(x['usd'] for x in b):+,.0f} R/tr {np.mean([x['R'] for x in b]) if b else 0:+.3f}")
print('  by OB: ' + ' '.join(f"{o} {sum(1 for x in tr if x['otf'] == o)}/{sum(x['usd'] for x in tr if x['otf'] == o):+,.0f}" for o in A['OTF'].split(',')))
print('  months: ' + ' '.join(f"{k} {v:+.0f}" for k, v in sorted(mon.items())))
if A['Out']: json.dump(tr, open(A['Out'], 'w'))
