"""Breakdowns of an s_obr.py trade dump.
    python an_obr.py obr.json [Cap=15] [Tgt=1] [OTF=H1] [LTF=M5] [CT=lux]   (filters optional)"""
import sys, json, numpy as np, datetime as dt

A = dict(Cap='15', Tgt='1', OTF='', LTF='', CT='', Comm=0.07)
f = sys.argv[1]
for a in sys.argv[2:]:
    k, v = a.split('='); A[k] = v
D = json.load(open(f)); T = D['trades']; U = dt.timezone.utc; SPL = 1761955200
T = [r for r in T if (not A['OTF'] or r['otf'] in A['OTF'].split(',')) and (not A['LTF'] or r['ltf'] in A['LTF'].split(','))
     and (not A['CT'] or r['ct'] == A['CT'])]
cap = A['Cap']; tgt = A['Tgt']
def R(r, tg=tgt):
    x = r['c' + cap]; risk = x['risk']
    if tg == 'align':
        if r['alg'] is None: return None
        dist = r['alg']
    else: dist = float(tg) * risk
    if x['mfe'] >= dist: return (dist - A['Comm']) / risk
    if x['slhit']: return (-risk - A['Comm']) / risk
    return (x['last'] - A['Comm']) / risk
for r in T: r['R'] = R(r)
T = [r for r in T if r['R'] is not None]
ist = lambda t: dt.datetime.fromtimestamp(t + 19800, U)
def sess(t):
    m = ist(t).hour * 60 + ist(t).minute
    return '1 Asia 05:30-12:30' if 330 <= m < 750 else '2 London 12:30-18:00' if 750 <= m < 1080 else '3 NY 18:00-00:00' if m >= 1080 else '0 Late 00:00-05:30'
def row(lab, L):
    if not L: return
    v = np.array([r['R'] for r in L]); a = [r['R'] for r in L if r['t'] < SPL]; b = [r['R'] for r in L if r['t'] >= SPL]
    print(f"  {lab:24s} n {len(v):5d} win {np.mean(v > 0)*100:4.0f}% R/tr {v.mean():+.3f} netR {v.sum():+7.1f} | Y1 {len(a):4d} {np.mean(a) if a else 0:+.3f} | Y2 {len(b):4d} {np.mean(b) if b else 0:+.3f}")
def grp(name, key, order=None):
    print(name); g = {}
    for r in T: g.setdefault(key(r), []).append(r)
    for k in (order or sorted(g)): row(str(k), g.get(k, []))
bk = lambda v, e: next((f"<{x}" for x in e if v < x), f">={e[-1]}")
bko = lambda e: [f"<{x}" for x in e] + [f">={e[-1]}"]
print(f"filters OTF={A['OTF'] or 'all'} LTF={A['LTF'] or 'all'} CT={A['CT'] or 'all'} | SL cap {cap} | target {tgt}R" if tgt != 'align' else '| target align')
row('ALL', T)
grp('OB timeframe', lambda r: r['otf'], ['H4', 'H2', 'H1', 'M30', 'M15', 'M10', 'M5'])
grp('direction', lambda r: 'BUY' if r['d'] > 0 else 'SELL')
grp('session (IST, at entry)', lambda r: sess(r['t']))
grp('IST hour', lambda r: ist(r['t']).hour)
grp('weekday', lambda r: ist(r['t']).strftime('%a'))
grp('raw OB stop (pts)', lambda r: bk(r['raw'], [3, 5, 8, 12, 15, 20, 30]), bko([3, 5, 8, 12, 15, 20, 30]))
grp('stop capped?', lambda r: 'capped' if r['raw'] > float(cap) else 'OB stop')
grp('OB height (pts)', lambda r: bk(r['obsz'], [1, 2, 4, 8, 15]), bko([1, 2, 4, 8, 15]))
grp('retest -> CISD delay (min)', lambda r: bk(r['delay'], [5, 15, 30, 60, 120]), bko([5, 15, 30, 60, 120]))
grp('OB age at retest (h)', lambda r: bk((r['rt'] - r['formed']) / 3600, [1, 4, 12, 24, 72]), bko([1, 4, 12, 24, 72]))
grp('room to aligning level (R)', lambda r: 'none' if r['alg'] is None else bk(r['alg'] / r['c' + cap]['risk'], [0.5, 1, 2, 3]), bko([0.5, 1, 2, 3]) + ['none'])
print('went in favour, then came back to the SL (share of ALL signals that hit SL after reaching x R):')
for x in (0.5, 1, 1.5, 2, 3):
    reach = [r for r in T if r['c' + cap]['mfe'] >= x * r['c' + cap]['risk']]
    back = [r for r in reach if r['c' + cap]['slhit']]
    print(f"   reached {x:3.1f}R: {len(reach)/len(T)*100:5.1f}%   of those later hit SL: {len(back)/max(1,len(reach))*100:5.1f}%")
mf = np.array([r['c' + cap]['mfe'] / r['c' + cap]['risk'] for r in T])
print('MFE (R) percentiles 25/50/75/90:', np.percentile(mf, [25, 50, 75, 90]).round(2), '| never reached +0.25R:', f"{np.mean(mf < 0.25)*100:.0f}%")
mon = {}
for r in T: mon.setdefault(ist(r['t']).strftime('%y-%m'), []).append(r['R'])
print('months (net R): ' + ' '.join(f"{k} {sum(v):+.0f}" for k, v in sorted(mon.items())), '| losing', sum(sum(v) < 0 for v in mon.values()), '/', len(mon))
