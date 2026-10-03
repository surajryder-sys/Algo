"""Month-by-month side-by-side for the combined test EAs (test5.x = V6S-ICT v2.23 + OB retest slot).

    python test5_compare.py split  <report.xlsx>               -> print v2.23 / OB / combined per month of a tester report
    python test5_compare.py add    <report.xlsx> <column tag>  -> add that report's three columns to test5_monthly.csv

A report is split by the OB slot's order comments ("T5 OB ..." test5.0, "T51 OB ..." test5.1, "V6S-ICT-OB-H1/H2" test5.2+): the OB entries,
their exits (matched by the order's own SL/TP price in the exit comment) and their commission are the OB slot; every
other deal is v2.23 (magics 26092701 / 26092731 / 26092721). Months are server time (UTC).
test5_monthly.csv holds the saved columns (tester runs and simulation estimates)."""
import sys, re, csv, collections, os
import openpyxl

CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test5_monthly.csv')

def split(path):
    ws = openpyxl.load_workbook(path, read_only=True).active
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    oi = [i for i, r in enumerate(rows) if r[0] == 'Orders'][0]; di = [i for i, r in enumerate(rows) if r[0] == 'Deals'][0]
    isob = lambda c: bool(re.match(r'T5\d* OB|V6S-ICT-OB', str(c or '')))
    sltp = {r[1]: (float(r[7]), float(r[8])) for r in rows[oi + 2:di] if r[0] and isob(r[12])}
    allm = collections.defaultdict(float); obm = collections.defaultdict(float); openob = []
    for r in rows[di + 2:]:
        if not r[0] or r[3] == 'balance' or r[4] not in ('in', 'out'): continue
        m = r[0][:7].replace('.', '-'); v = (r[8] or 0) + (r[9] or 0) + (r[10] or 0); allm[m] += v
        if r[4] == 'in' and isob(r[12]):
            sl, tp = sltp[r[7]]; openob.append((sl, tp, r[3])); obm[m] += v
        elif r[4] == 'out':
            mm = re.match(r'(sl|tp) ([\d.]+)', str(r[12] or ''))
            if mm:
                p = float(mm.group(2))
                for k, (sl, tp, side) in enumerate(openob):
                    if (abs(sl - p) < .002 or abs(tp - p) < .002) and (side == 'buy') == (r[3] == 'sell'):
                        obm[m] += v; openob.pop(k); break
    return {m: (allm[m] - obm[m], obm[m], allm[m]) for m in sorted(allm)}

def load():
    if not os.path.exists(CSV): return [], {}
    with open(CSV, newline='') as f:
        rd = list(csv.reader(f))
    return rd[0][1:], {r[0]: r[1:] for r in rd[1:]}

def show(cols, data):
    print('month   ' + ' '.join(f'{c:>14s}' for c in cols))
    for m in sorted(data):
        print(f'{m:8s}' + ' '.join(f'{v:>14s}' for v in data[m]))
    tot = ['total   ']; neg = ['losing  ']
    for j in range(len(cols)):
        vals = [float(data[m][j]) for m in data if j < len(data[m]) and data[m][j] not in ('', None)]
        tot.append(f'{sum(vals):>+14,.0f}'); neg.append(f'{sum(v < 0 for v in vals):>14d}')
    print(' '.join(tot)); print(' '.join(neg))

if __name__ == '__main__':
    if sys.argv[1] == 'split':
        sp = split(sys.argv[2]); show(['v2.23', 'OB slot', 'combined'], {m: [f'{a:+.0f}', f'{b:+.0f}', f'{c:+.0f}'] for m, (a, b, c) in sp.items()})
    elif sys.argv[1] == 'add':
        sp = split(sys.argv[2]); tag = sys.argv[3]; cols, data = load()
        new = [f'{tag} v2.23', f'{tag} OB', f'{tag} combined']
        months = sorted(set(data) | set(sp))
        for m in months:
            row = data.get(m, [''] * len(cols)); a, b, c = sp.get(m, (None, None, None))
            data[m] = row + ([f'{a:+.0f}', f'{b:+.0f}', f'{c:+.0f}'] if a is not None else ['', '', ''])
        cols = cols + new
        with open(CSV, 'w', newline='') as f:
            w = csv.writer(f); w.writerow(['month'] + cols); [w.writerow([m] + data[m]) for m in months]
        show(cols, data)
