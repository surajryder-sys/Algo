"""Pack / unpack the XAUUSD real-tick file (ticks.npy, ~915 MB) into 3 lossless compressed parts that fit GitHub's
100 MB file limit.  ticks.npy = float64 array (N, 3): [time_ms, bid, ask], Exness Trial12 XAUUSD, 1 Apr - 25 Sep 2026.

    python ticks_pack.py unpack     # data/ticks_part*.npz  ->  ticks.npy  (run this once after cloning)
    python ticks_pack.py pack       # ticks.npy  ->  data/ticks_part*.npz
Prices are stored as integer thousandths (3 decimals, exact for XAUUSD); times as millisecond deltas."""
import sys, glob, numpy as np

def pack(src='ticks.npy', parts=3):
    tk = np.load(src, mmap_mode='r'); n = len(tk)
    for p, (a, b) in enumerate(zip(np.linspace(0, n, parts + 1).astype(int)[:-1], np.linspace(0, n, parts + 1).astype(int)[1:]), 1):
        t = tk[a:b, 0].astype(np.int64); bid = np.round(tk[a:b, 1] * 1000).astype(np.int64); ask = np.round(tk[a:b, 2] * 1000).astype(np.int64)
        np.savez_compressed(f'data/ticks_part{p}.npz', t0=t[0], b0=bid[0], dt=np.diff(t, prepend=t[0]).astype(np.int32),
                            db=np.diff(bid, prepend=bid[0]).astype(np.int32), sp=(ask - bid).astype(np.int32))
        print('wrote', f'data/ticks_part{p}.npz', b - a, 'ticks')

def unpack(dst='ticks.npy'):
    out = []
    for f in sorted(glob.glob('data/ticks_part*.npz')):
        z = np.load(f); t = z['t0'] + np.cumsum(z['dt'].astype(np.int64)) - z['dt'][0]; bid = z['b0'] + np.cumsum(z['db'].astype(np.int64)) - z['db'][0]
        out.append(np.column_stack([t.astype(np.float64), bid / 1000.0, (bid + z['sp']) / 1000.0]))
    tk = np.concatenate(out); np.save(dst, tk); print('wrote', dst, tk.shape)

if __name__ == '__main__':
    {'pack': pack, 'unpack': unpack}[sys.argv[1]]()
