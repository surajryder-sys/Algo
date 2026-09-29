"""Combine the two EA slots' trade dumps into one account result (how every "full EA" number in HANDOVER.md was made).

    python combine.py AL_OLD AL_NEW DZ_OLD DZ_NEW [BALANCE] [DZ_BOOK]
      AL_*  = TrOut json of s_v6.py (aligning-level slot: list of trades, typ REV / BO / LEG)
      DZ_*  = TrOut json of s_dz.py (DZ slot: dict of books -> trades; default book LEVEL)
Prints the 2-year net, worst drop (closed-trade equity, % of peak), both periods, the split by trade type and every month
(months by exit time in IST)."""
import sys, json, numpy as np, datetime as dt

IST = dt.timedelta(hours=5, minutes=30)
mk = lambda t: (dt.datetime.fromtimestamp(t, dt.timezone.utc) + IST).strftime('%Y-%m')

def stats(tr, bal):
    tr = sorted(tr, key=lambda x: x['te']); pl = np.array([x['pl'] for x in tr])
    eq = bal + np.cumsum(pl); pk = np.maximum.accumulate(np.concatenate([[bal], eq]))[1:]
    return pl.sum(), (pk - eq).max(), ((pk - eq) / pk).max() * 100, eq.min(), len(tr)

def main():
    a_old, a_new, z_old, z_new = [json.load(open(p)) for p in sys.argv[1:5]]
    bal = float(sys.argv[5]) if len(sys.argv) > 5 else 77581.0
    book = sys.argv[6] if len(sys.argv) > 6 else 'LEVEL'
    zo, zn = z_old[book], z_new[book]
    for x in zo + zn: x['typ'] = x.get('typ') if x.get('typ') == 'LEG' else 'DZ'
    o = stats(a_old + zo, bal); n = stats(a_new + zn, bal); c = stats(a_old + a_new + zo + zn, bal)
    print(f"2 years: net {c[0]:+,.0f}  worst drop {c[1]:,.0f} ({c[2]:.1f}% of peak)  lowest balance {c[3]:,.0f}  trades {c[4]}")
    print(f"old period {o[0]:+,.0f} (drop {o[1]:,.0f})  |  recent period {n[0]:+,.0f} (drop {n[1]:,.0f})")
    parts = {}
    for x in a_old + a_new + zo + zn: parts[x['typ']] = parts.get(x['typ'], 0) + x['pl']
    print("split: " + "  ".join(f"{k} {v:+,.0f}" for k, v in sorted(parts.items())))
    mon = {}
    for x in a_old + a_new + zo + zn: mon[mk(x['te'])] = mon.get(mk(x['te']), 0) + x['pl']
    print("months: " + "  ".join(f"{k} {v:+,.0f}" for k, v in sorted(mon.items())))

if __name__ == '__main__':
    main()
