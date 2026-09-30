"""Build the Exness Trial12 gold data the simulations now use (2026-09-30) -- the SAME feed as the MT5 Strategy Tester runs
on the MetaTrader5-5 terminal (Exness-MT5Trial12, XAUUSD), so sim trades match the tester (~96% of entries, Jan-Sep 2026).

    python build_t12.py ticks   ->  ticks_t12.npy  (real ticks 2026-01-01 .. 2026-09-28 via the MetaTrader5 package; the
                                     terminal must be running; 72,847,025 ticks = the tester's count)
    python build_t12.py bars    ->  m1_t12.npy     (M1: Trial12 .hcc 2022-2025 with the bad 00:00 records dropped, + 2026
                                     bars built from ticks_t12.npy, as the tester builds them)
The terminal locks the current year's .hcc, which is why 2026 comes from ticks. If the terminal is running, copy the
2022-2025 .hcc files somewhere first and point HCC at that folder.

Use:  s_v6.py / s_dz.py ... Data=m1_t12.npy Ticks=ticks_t12.npy  with  Start=2024-10-01 End=2026-01-01 TickSrc=ohlc  (bars)
      and  Start=2026-01-01 End=2026-09-29 TickSrc=real  (real ticks)."""
import sys, numpy as np, datetime as dt
from build_data import parse, drop_bad_midnight
from ohlc import m1_from_ticks

HCC = r"C:\Users\ARK\AppData\Roaming\MetaQuotes\Terminal\4DB333B24A74B726D7AA441A9D0137DC\bases\Exness-MT5Trial12\history\XAUUSD"
D = np.dtype([('time', '<i8'), ('open', '<f8'), ('high', '<f8'), ('low', '<f8'), ('close', '<f8')])
f = lambda t: dt.datetime.fromtimestamp(int(t), dt.timezone.utc).strftime('%Y-%m-%d %H:%M')

def ticks():
    import MetaTrader5 as mt5
    U = dt.timezone.utc
    mt5.initialize(path=r"C:\Program Files\MetaTrader5-5\terminal64.exe"); mt5.symbol_select('XAUUSD', True)
    tk = []
    for mo in range(1, 10):
        a = dt.datetime(2026, mo, 1, tzinfo=U); b = dt.datetime(2026, mo + 1, 1, tzinfo=U) if mo < 9 else dt.datetime(2026, 9, 29, tzinfo=U)
        t = mt5.copy_ticks_range('XAUUSD', a, b, mt5.COPY_TICKS_ALL)
        if t is not None and len(t): tk.append(np.column_stack([t['time_msc'].astype(np.float64), t['bid'], t['ask']]))
    mt5.shutdown()
    q = np.concatenate(tk); q = q[(q[:, 1] > 0) & (q[:, 2] > 0)]
    np.save('ticks_t12.npy', q); print('ticks', len(q), f(q[0, 0] / 1000), '->', f(q[-1, 0] / 1000))

def bars():
    h = parse(HCC, (2022, 2023, 2024, 2025), 500, 20000)
    A = np.empty(len(h), dtype=D)
    for k in D.names: A[k] = h[k]
    A, nbad = drop_bad_midnight(A)
    b = m1_from_ticks(np.load('ticks_t12.npy')); B = np.empty(len(b), dtype=D)
    for k in D.names: B[k] = b[k]
    out = np.concatenate([A[A['time'] < B['time'][0]], B]); np.save('m1_t12.npy', out)
    print('M1 bars', len(out), f(out['time'][0]), '->', f(out['time'][-1]), '| bad 00:00 bars removed', nbad)

if __name__ == '__main__':
    {'ticks': ticks, 'bars': bars}[sys.argv[1]]()
