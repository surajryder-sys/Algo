import numpy as np, sim
from multiprocessing import Pool
SPREAD = 0.09

def synth_ticks(m1):
    o, h, l, c = m1['open'], m1['high'], m1['low'], m1['close']
    up = c >= o
    p2 = np.where(up, l, h); p3 = np.where(up, h, l)
    t = m1['time'].astype(np.float64) * 1000
    T = np.stack([t, t + 15000, t + 30000, t + 45000], axis=1).ravel()
    P = np.stack([o, p2, p3, c], axis=1).ravel()
    return np.column_stack([T, P, P + SPREAD])

def m1_from_ticks(tk):
    t = (tk[:, 0] // 60000).astype(np.int64) * 60; bid = tk[:, 1]
    idx = np.flatnonzero(np.diff(t)) + 1; st = np.concatenate([[0], idx]); en = np.concatenate([idx, [len(t)]])
    d = np.dtype([('time', '<i8'), ('open', '<f8'), ('high', '<f8'), ('low', '<f8'), ('close', '<f8')])
    r = np.empty(len(st), dtype=d); r['time'] = t[st]; r['open'] = bid[st]; r['close'] = bid[en - 1]
    r['high'] = np.maximum.reduceat(bid, st); r['low'] = np.minimum.reduceat(bid, st); return r

BASE = dict(BasketMaxLossMoney=0, UseHTFFilter=False, NoTradeWindows=[(745,765)], WeekOpenBlockMinutes=60, MaxSpreadPrice=0.5, LotUnit=0.20, BasketTargetMoney=100.0, Deposit=200000.0, MaxExtraLegs=1, HedgeRelease=True)
PICKS = {'M5 dual-ATR + CISD': dict(tf='m5'), 'M3 CISD-only': dict(tf='m3', UseFlips=False, UseState=False)}

def job(a):
    period, name = a
    o = dict(PICKS[name]); tf = o.pop('tf')
    if period == 'calib Apr13-Sep24 (OHLC)':
        tk = np.load('ticks.npy'); m1 = m1_from_ticks(tk)
        data = dict(m5=np.load('m5.npy'), m3=np.load('m3t.npy'), m15=np.load('m15.npy'), ticks=synth_ticks(m1))
        S, E = '2026-04-13', '2026-09-25'
    else:
        m1 = np.load('m1_clean.npy')
        data = dict(m5=np.load('m5h.npy'), m3=np.load('m3h.npy'), m15=np.load('m15.npy'), ticks=synth_ticks(m1))
        S, E = '2025-10-01', '2026-04-01'
    P = dict(sim.DEFAULTS); P.update(BASE); P.update(o); P['SignalTF'] = tf; P['Start'], P['End'] = S, E
    r = sim.run(P, data)
    m = {}
    for d, b, e in r['daily']: m[d[:7]] = b
    prev = 200000; mon = []
    for k, v in m.items(): mon.append(f"{k[2:]}:{(v-prev)/prev*100:+.1f}%"); prev = v
    return period, name, r, mon

if __name__ == '__main__':
    jobs = [(p, n) for p in ('calib Apr13-Sep24 (OHLC)', 'Oct 2025 - Mar 2026 (OHLC)') for n in PICKS]
    with Pool(4) as pool: res = pool.map(job, jobs)
    for p, n, r, mon in res:
        pr = r['final'] - 200000
        print(f"{p:28s} {n:20s} {pr/2000:+7.1f}%  drop {r['maxDD']:7.0f}  low {r['minEq']:7.0f}  lots {r['maxVol'][0]:5.1f}  baskets {len(r['closes']):4d}  p/d {pr/r['maxDD']:4.1f}\n      {' '.join(mon)}")
