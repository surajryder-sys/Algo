"""Gold M1 bars WITH tick volume for the order-block sim (s_ob.py) -> m1_2y_tv.npy.

Same source + cleanup as build2y.py (Exness Real7 XAUUSD .hcc from the live terminal's data folder) but keeps the
tick-volume column the order block detector needs. After the .hcc history ends, bars and tick volume (= tick count
per minute) come from the real-tick file ticks.npy (Exness Trial12). Unlike build2y.py, tick-built bars are used for
EVERYTHING from the first real tick (2026-04-01) on.

Data fix (2026-09-30): the .hcc parse also picks up D1 records -- at 00:00 UTC of almost every day the "M1 bar" is
really the whole day's candle (full daily range, ~100k+ ticks). data/m1_2y.npy (build2y.py) has the same ~600 bars.
Here each such bar is replaced by a neutral bar (prev close -> next open, local median volume)."""
import numpy as np, datetime as dt
base = r"C:\Users\ARK\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\bases\Exness-MT5Real7\history\XAUUSD"
rec = np.dtype([('time','<i8'),('open','<f8'),('high','<f8'),('low','<f8'),('close','<f8'),('tv','<i8'),('spread','<i4'),('rv','<i8')])
D = np.dtype([('time','<i8'),('open','<f8'),('high','<f8'),('low','<f8'),('close','<f8'),('tv','<i8')])
parts = []
for y in (2024, 2025, 2026):
    b = open(base + "\\" + f"{y}.hcc", 'rb').read()
    lo = int(dt.datetime(y,1,1,tzinfo=dt.timezone.utc).timestamp()); hi = int(dt.datetime(y+1,1,1,tzinfo=dt.timezone.utc).timestamp())
    got = []
    for a in range(60):
        n = (len(b) - a) // 60
        r = np.frombuffer(b[a:a+n*60], dtype=rec)
        with np.errstate(invalid='ignore'):
            ok = (r['time']>=lo)&(r['time']<hi)&(r['time']%60==0)&(r['low']>100)&(r['high']<20000)&(r['high']>=r['low'])&(r['high']>=np.maximum(r['open'],r['close'])-1e-9)&(r['low']<=np.minimum(r['open'],r['close'])+1e-9)
        inc = np.concatenate([[False], r['time'][1:] > r['time'][:-1]])
        if (ok&inc).any(): got.append(r[ok&inc])
    cat = np.concatenate(got); _, idx = np.unique(cat['time'], return_index=True); parts.append(cat[idx])
m1 = np.concatenate(parts); _, idx = np.unique(m1['time'], return_index=True); m1 = m1[idx]
o,h,l,c = m1['open'],m1['high'],m1['low'],m1['close']
pc = np.concatenate([[c[0]],c[:-1]]); no = np.concatenate([o[1:],[o[-1]]])
spike = (np.abs((o+c)/2-(pc+no)/2)>30)&(np.abs(pc-no)<15)
wh = (h-np.maximum(o,c)>40)&(h-np.maximum(pc,no)>40); wl = (np.minimum(o,c)-l>40)&(np.minimum(pc,no)-l>40)
m1 = m1.copy(); m1['high'][wh] = np.maximum(o,c)[wh]; m1['low'][wl] = np.minimum(o,c)[wl]; m1 = m1[~spike]
hist = np.empty(len(m1), dtype=D)
for f in D.names: hist[f] = m1[f]
import numpy.lib.stride_tricks as st
tvf = hist['tv'].astype(float); rg = hist['high'] - hist['low']
mtv = np.median(st.sliding_window_view(np.pad(tvf, 10, mode='edge'), 21), axis=1)
mrg = np.median(st.sliding_window_view(np.pad(rg, 10, mode='edge'), 21), axis=1)
d1 = np.flatnonzero((hist['time'] % 86400 == 0) & ((tvf > 10 * np.maximum(mtv, 1)) | (rg > 8 * np.maximum(mrg, 0.05))))
d1 = d1[(d1 > 0) & (d1 < len(hist) - 1)]
po = hist['close'][d1 - 1]; nx = hist['open'][d1 + 1]
hist['open'][d1] = po; hist['close'][d1] = nx; hist['high'][d1] = np.maximum(po, nx); hist['low'][d1] = np.minimum(po, nx)
hist['tv'][d1] = mtv[d1].astype(np.int64)
wknd = ((hist['time'] // 86400) % 7 == 2) | (((hist['time'] // 86400) % 7 == 3) & (hist['time'] % 86400 < 20 * 3600))
hist = hist[~wknd]   # Saturday / Sunday-before-20:00-UTC bars (leaked D1 records at Sun 00:00) -- gold is closed
tk = np.load('ticks.npy'); t = (tk[:, 0] // 60000).astype(np.int64) * 60; bid = tk[:, 1]
i = np.flatnonzero(np.diff(t)) + 1; st = np.concatenate([[0], i]); en = np.concatenate([i, [len(t)]])
tm = np.empty(len(st), dtype=D); tm['time'] = t[st]; tm['open'] = bid[st]; tm['close'] = bid[en-1]
tm['high'] = np.maximum.reduceat(bid, st); tm['low'] = np.minimum.reduceat(bid, st); tm['tv'] = en - st
cut = int(tm['time'][0])
out = np.concatenate([hist[hist['time'] < cut], tm])
np.save('m1_2y_tv.npy', out)
f = lambda x: dt.datetime.fromtimestamp(int(x), dt.timezone.utc).strftime('%Y-%m-%d')
print('M1', len(out), f(out['time'][0]), '->', f(out['time'][-1]), '| hcc until', f(cut), '| D1 bars repaired', len(d1), '| weekend bars dropped', int(wknd.sum()),
      '| zero-volume bars', int((out['tv'] == 0).sum()))
