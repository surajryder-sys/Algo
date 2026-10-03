"""Gold data from the Exness REAL account (Exness-MT5Real7, XAUUSD) via the MetaTrader5 package (read-only), for the
order-block sims (s_obc.py).

    python build_real7.py ticks   ->  ticks_real7.npz   real ticks 2024-10-01 .. now, month by month
                                      (t = ms int64, b = bid x1000 int32, s = (ask-bid) x1000 int32)
    python build_real7.py bars    ->  m1_real7_tv.npy   M1 bars built from those ticks; tv = ticks per minute
    python build_real7.py extend  ->  ticks_real7x.npz + m1_real7x_tv.npy  (adds Jan - Sep 2024 in front)
The 'MetaTrader 5' terminal must be running and logged into the Real7 account. Nothing is traded."""
import sys, numpy as np, datetime as dt
U = dt.timezone.utc
TERM = r"C:\Program Files\MetaTrader 5\terminal64.exe"
FROM = dt.datetime(2024, 10, 1, tzinfo=U)

def ticks():
    import MetaTrader5 as mt5
    assert mt5.initialize(path=TERM), mt5.last_error()
    a = mt5.account_info(); print('account', a.server, a.login, 'REAL' if a.trade_mode == 2 else a.trade_mode)
    mt5.symbol_select('XAUUSD', True)
    T, B, S = [], [], []
    m = FROM; now = dt.datetime.now(U)
    while m < now:
        n = (m.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        got = None
        for _ in range(3):
            got = mt5.copy_ticks_range('XAUUSD', m, min(n, now), mt5.COPY_TICKS_ALL)
            if got is not None and len(got): break
        if got is None or not len(got): print(m.date(), 'NO TICKS', mt5.last_error()); m = n; continue
        bid, ask = got['bid'], got['ask']; ok = (bid > 0) & (ask > 0)
        T.append(got['time_msc'][ok].astype(np.int64)); B.append(np.round(bid[ok] * 1000).astype(np.int32))
        S.append(np.round((ask[ok] - bid[ok]) * 1000).astype(np.int32))
        print(m.strftime('%Y-%m'), len(T[-1]), 'ticks'); m = n
    mt5.shutdown()
    t = np.concatenate(T); o = np.argsort(t, kind='stable')
    np.savez('ticks_real7.npz', t=t[o], b=np.concatenate(B)[o], s=np.concatenate(S)[o])
    print('saved ticks_real7.npz', len(t))

def load_ticks():
    z = np.load('ticks_real7.npz'); t = z['t'].astype(np.float64); b = z['b'] / 1000.0
    return np.column_stack([t, b, b + z['s'] / 1000.0])

def bars(src='ticks_real7.npz', dst='m1_real7_tv.npy'):
    z = np.load(src); t = (z['t'] // 60000) * 60; b = z['b'] / 1000.0
    i = np.flatnonzero(np.diff(t)) + 1; st = np.concatenate([[0], i]); en = np.concatenate([i, [len(t)]])
    D = np.dtype([('time', '<i8'), ('open', '<f8'), ('high', '<f8'), ('low', '<f8'), ('close', '<f8'), ('tv', '<i8')])
    r = np.empty(len(st), dtype=D); r['time'] = t[st]; r['open'] = b[st]; r['close'] = b[en - 1]
    r['high'] = np.maximum.reduceat(b, st); r['low'] = np.minimum.reduceat(b, st); r['tv'] = en - st
    np.save(dst, r)
    f = lambda x: dt.datetime.fromtimestamp(int(x), U).strftime('%Y-%m-%d %H:%M')
    print('M1', len(r), f(r['time'][0]), '->', f(r['time'][-1]))

def extend():
    """Jan - Sep 2024 real ticks + ticks_real7.npz -> ticks_real7x.npz and m1_real7x_tv.npy (Jan 2024 -> now)."""
    import MetaTrader5 as mt5
    assert mt5.initialize(path=TERM), mt5.last_error()
    mt5.symbol_select('XAUUSD', True)
    T, B, S = [], [], []; m = dt.datetime(2024, 1, 1, tzinfo=U)
    while m < FROM:
        n = (m.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        got = mt5.copy_ticks_range('XAUUSD', m, n, mt5.COPY_TICKS_ALL)
        bid, ask = got['bid'], got['ask']; ok = (bid > 0) & (ask > 0)
        T.append(got['time_msc'][ok].astype(np.int64)); B.append(np.round(bid[ok] * 1000).astype(np.int32))
        S.append(np.round((ask[ok] - bid[ok]) * 1000).astype(np.int32)); print(m.strftime('%Y-%m'), len(T[-1]), 'ticks'); m = n
    mt5.shutdown()
    z = np.load('ticks_real7.npz'); T.append(z['t']); B.append(z['b']); S.append(z['s'])
    t = np.concatenate(T); o = np.argsort(t, kind='stable')
    np.savez('ticks_real7x.npz', t=t[o], b=np.concatenate(B)[o], s=np.concatenate(S)[o]); print('saved ticks_real7x.npz', len(t))
    bars('ticks_real7x.npz', 'm1_real7x_tv.npy')

if __name__ == '__main__':
    {'ticks': ticks, 'bars': bars, 'extend': extend}[sys.argv[1]]()
