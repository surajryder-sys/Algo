import numpy as np
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

