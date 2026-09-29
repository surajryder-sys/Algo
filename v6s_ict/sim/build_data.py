"""Rebuild 1-minute bar files from MetaTrader 5 history (.hcc) files on this PC -- how data/btc_m1.npy, eth_m1.npy and
oil_m1.npy were made (2026-09-29/30). Gold's m1_2y.npy was made by build2y.py (Exness Real7 XAUUSD .hcc + real ticks).

    python build_data.py BTC      ->  btc_m1.npy   (Real7 to 2026-07-07, then Trial12 to 2026-09-26)
    python build_data.py ETH      ->  eth_m1.npy   (Real7 to 2026-07-27, then Trial12 to 2026-08-27)
    python build_data.py OIL      ->  oil_m1.npy   (Trial7 to 2026-03-02, then Trial12 to 2026-08-07)

The .hcc record is 60 bytes (time, open, high, low, close, tick volume, spread, real volume) but the file's header offset
varies, so every byte offset 0..59 is tried and only rows that look like valid, increasing M1 bars are kept. Spikes
(single bars far away from both neighbours) are removed and freak wicks trimmed; thresholds scale with the price."""
import sys, numpy as np, datetime as dt

T = r"C:\Users\ARK\AppData\Roaming\MetaQuotes\Terminal"
CFG = {
    'BTC': dict(out='btc_m1.npy', lo=5000, hi=500000, S=20.0, srcs=[
        (T + r"\2A968AC344F5A9A1B3E3DB21B82AC033\bases\Exness-MT5Real7\history\BTCUSD", (2024, 2025, 2026)),
        (T + r"\4DB333B24A74B726D7AA441A9D0137DC\bases\Exness-MT5Trial12\history\BTCUSD", (2025, 2026))]),
    'ETH': dict(out='eth_m1.npy', lo=300, hi=20000, S=1.0, srcs=[
        (T + r"\18BADC67994D2B45C82D43724355D20B\bases\Exness-MT5Real7\history\ETHUSD", (2024, 2025, 2026)),
        (T + r"\4DB333B24A74B726D7AA441A9D0137DC\bases\Exness-MT5Trial12\history\ETHUSD", (2026,))]),
    'OIL': dict(out='oil_m1.npy', lo=20, hi=300, S=0.05, srcs=[
        (T + r"\4DB333B24A74B726D7AA441A9D0137DC\bases\Exness-MT5Trial7\history\USOIL", (2024, 2025, 2026)),
        (T + r"\2A968AC344F5A9A1B3E3DB21B82AC033\bases\Exness-MT5Trial12\history\USOIL", (2026,))]),
}
REC = np.dtype([('time', '<i8'), ('open', '<f8'), ('high', '<f8'), ('low', '<f8'), ('close', '<f8'), ('tv', '<i8'), ('spread', '<i4'), ('rv', '<i8')])

def parse(base, years, lo_px, hi_px):
    parts = []
    for y in years:
        try: b = open(base + "\\" + f"{y}.hcc", 'rb').read()
        except FileNotFoundError: continue
        lo = int(dt.datetime(y, 1, 1, tzinfo=dt.timezone.utc).timestamp()); hi = int(dt.datetime(y + 1, 1, 1, tzinfo=dt.timezone.utc).timestamp())
        got = []
        for a in range(60):
            n = (len(b) - a) // 60; r = np.frombuffer(b[a:a + n * 60], dtype=REC)
            with np.errstate(invalid='ignore'):
                ok = ((r['time'] >= lo) & (r['time'] < hi) & (r['time'] % 60 == 0) & (r['low'] > lo_px) & (r['high'] < hi_px) & (r['high'] >= r['low'])
                      & (r['high'] >= np.maximum(r['open'], r['close']) - 1e-9) & (r['low'] <= np.minimum(r['open'], r['close']) + 1e-9))
            inc = np.concatenate([[False], r['time'][1:] > r['time'][:-1]])
            if (ok & inc).any(): got.append(r[ok & inc])
        if got:
            cat = np.concatenate(got); _, idx = np.unique(cat['time'], return_index=True); parts.append(cat[idx])
    m = np.concatenate(parts); _, idx = np.unique(m['time'], return_index=True); return m[idx]

def build(name):
    c = CFG[name]
    a = parse(*c['srcs'][0], c['lo'], c['hi']); b = parse(*c['srcs'][1], c['lo'], c['hi']); cut = a['time'][-1]
    m1 = np.concatenate([a, b[b['time'] > cut]])
    o, h, l, cl = m1['open'], m1['high'], m1['low'], m1['close']
    pc = np.concatenate([[cl[0]], cl[:-1]]); no = np.concatenate([o[1:], [o[-1]]]); S = c['S']
    spike = (np.abs((o + cl) / 2 - (pc + no) / 2) > 30 * S) & (np.abs(pc - no) < 15 * S)
    wh = (h - np.maximum(o, cl) > 40 * S) & (h - np.maximum(pc, no) > 40 * S); wl = (np.minimum(o, cl) - l > 40 * S) & (np.minimum(pc, no) - l > 40 * S)
    m1 = m1.copy(); m1['high'][wh] = np.maximum(o, cl)[wh]; m1['low'][wl] = np.minimum(o, cl)[wl]; m1 = m1[~spike]
    d = np.dtype([('time', '<i8'), ('open', '<f8'), ('high', '<f8'), ('low', '<f8'), ('close', '<f8')])
    out = np.empty(len(m1), dtype=d)
    for f in d.names: out[f] = m1[f]
    np.save(c['out'], out)
    f = lambda t: dt.datetime.fromtimestamp(int(t), dt.timezone.utc).strftime('%Y-%m-%d')
    print(name, 'M1 bars', len(out), f(out['time'][0]), '->', f(out['time'][-1]), '| first source until', f(cut), '| spikes removed', int(spike.sum()))

if __name__ == '__main__':
    build(sys.argv[1].upper())
