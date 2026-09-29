import numpy as np, datetime as dt
from ohlc import m1_from_ticks
base = r"C:\Users\ARK\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\bases\Exness-MT5Real7\history\XAUUSD"
rec = np.dtype([('time','<i8'),('open','<f8'),('high','<f8'),('low','<f8'),('close','<f8'),('tv','<i8'),('spread','<i4'),('rv','<i8')])
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
d = np.dtype([('time','<i8'),('open','<f8'),('high','<f8'),('low','<f8'),('close','<f8')])
hist = np.empty(len(m1), dtype=d)
for f in d.names: hist[f] = m1[f]
tkm1 = m1_from_ticks(np.load('ticks.npy'))
cut = int(hist['time'][-1])
merged = np.concatenate([hist, tkm1[tkm1['time'] > cut]])
np.save('m1_2y.npy', merged)
def agg(m, sec):
    b = (m['time']//sec)*sec; idx = np.flatnonzero(np.diff(b))+1; st = np.concatenate([[0],idx]); en = np.concatenate([idx,[len(b)]])
    r = np.empty(len(st), dtype=d); r['time'] = b[st]; r['open'] = m['open'][st]; r['close'] = m['close'][en-1]
    r['high'] = np.maximum.reduceat(m['high'], st); r['low'] = np.minimum.reduceat(m['low'], st); return r
np.save('m5_2y.npy', agg(merged, 300)); np.save('m3_2y.npy', agg(merged, 180))
f = lambda t: dt.datetime.fromtimestamp(int(t), dt.timezone.utc).date()
print('M1 bars', len(merged), f(merged['time'][0]), '->', f(merged['time'][-1]), '| history until', f(cut), '| spikes removed', spike.sum())
