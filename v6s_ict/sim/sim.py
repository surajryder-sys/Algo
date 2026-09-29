"""Tick-level Python mirror of v5s_ict/V5S_ICT_EA.mq5 for fast variant testing.
Data: m3.npy / m15.npy (MT5 rates), ticks.npy [time_msc, bid, ask] from MT5-5 (read-only export)."""
import sys, json
import numpy as np

CONTRACT = 100.0
SWAP_LONG_PER_LOT = -550.4 * 0.1   # points * tick value per point per lot (Exness XAUUSD)
SWAP_SHORT_PER_LOT = 0.0

DEFAULTS = dict(
    KeyValue=2.0, ATRPeriod=2, KeyValue2=2.0, ATRPeriod2=300, CISDTolerance=0.7, WarmupBars=3000,
    LotUnit=0.01, MaxLadderStep=10, MaxExtraLegs=1,
    FirstTargetPrice=10.0, BasketTargetMoney=10.0, BreakevenFromStep=6, BreakevenMoney=0.0,
    BasketMaxLossMoney=100.0, HedgeRelease=False,
    UseHTFFilter=True, FilterTF='m15',
    NoTradeWindows=[(12*60+25, 12*60+45)], WeekOpenBlockMinutes=60, MaxSpreadPrice=0.50,
    Start='2026-09-01', End='2026-09-24', Deposit=1000.0,
)


def sma_atr(r, p):
    h, l, c = r['high'], r['low'], r['close']
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h, pc) - np.minimum(l, pc)
    tr[0] = h[0] - l[0]
    out = np.full(len(r), np.nan)
    cs = np.cumsum(tr)
    out[p-1:] = (cs[p-1:] - np.concatenate([[0], cs[:-p]])) / p
    return out


class Atr:
    def __init__(s, P, closes, a1, a2):
        s.P = P; s.c = closes; s.a1 = a1; s.a2 = a2
        s.have = False; s.t1 = s.t2 = s.pc = 0.0; s.conf = 0

    @staticmethod
    def trail(src, src1, prev, nl):
        if src > prev and src1 > prev: return max(prev, src - nl)
        if src < prev and src1 < prev: return min(prev, src + nl)
        if src > prev: return src - nl
        return src + nl

    def step(s, i):
        c = s.c[i]
        if not s.have:
            s.t1 = s.t2 = s.pc = c; s.have = True
        else:
            s.t1 = s.trail(c, s.pc, s.t1, s.P['KeyValue'] * s.a1[i])
            s.t2 = s.trail(c, s.pc, s.t2, s.P['KeyValue2'] * s.a2[i])
            s.pc = c
        n = s.conf
        if c > max(s.t1, s.t2): n = 1
        elif c < min(s.t1, s.t2): n = -1
        flip = 0
        if n != s.conf:
            if s.conf != 0: flip = n
            s.conf = n
        return flip


class Supertrend:
    """Port of CalcSupertrend in mql5/ATR_Trial_Dual_SuperTrend_Major_Minor_HammerStar.mq5
    (ATR 10 via MT5 iATR = SMA of TR, multiplier 3, source hl2). conf/step() mirror Atr."""
    def __init__(s, r, period=10, mult=3.0):
        s.h = r['high']; s.l = r['low']; s.c = r['close']; s.atr = sma_atr(r, period); s.m = mult
        s.up = s.dn = None; s.pc = None; s.conf = 0

    def step(s, i):
        src = (s.h[i] + s.l[i]) / 2.0; a = s.atr[i]; c = s.c[i]
        upRaw = src - s.m * a; dnRaw = src + s.m * a
        if s.up is None:
            s.up, s.dn, s.pc, s.conf = upRaw, dnRaw, c, 1
            return 0
        up1, dn1, cp = s.up, s.dn, s.pc
        s.up = max(upRaw, up1) if cp > up1 else upRaw
        s.dn = min(dnRaw, dn1) if cp < dn1 else dnRaw
        prev = s.conf
        if prev == -1 and c > dn1: s.conf = 1
        elif prev == 1 and c < up1: s.conf = -1
        s.pc = c
        return s.conf if s.conf != prev else 0


class Cisd:
    def __init__(s, tol):
        s.tol = tol; s.o = []; s.c = []; s.bear = []; s.bull = []   # candidates: list of (open, idx), [0] newest

    def step(s, o, c):
        i = len(s.c); s.o.append(o); s.c.append(c)
        if i >= 1:
            if s.c[i-1] < s.o[i-1] and c > o: s.bear.insert(0, (o, i))
            if s.c[i-1] > s.o[i-1] and c < o: s.bull.insert(0, (o, i))
        r = 0
        if s.conf_bear(i): r = -1
        if s.conf_bull(i): r = 1
        return r

    def conf_bear(s, i):
        C, O = s.c, s.o
        while s.bear:
            co, ci = s.bear[0]
            if C[i] < co:
                highest = max(0.0, max(C[ci:i+1]))
                top = 0.0; k = ci - 1
                while k >= 0 and C[k] < O[k]:
                    top = O[k]; k -= 1
                d = top - co
                if d != 0 and (highest - co) / d > s.tol:
                    s.bear = []; return True
                s.bear.pop(0)
            else:
                break
        return False

    def conf_bull(s, i):
        C, O = s.c, s.o
        while s.bull:
            co, ci = s.bull[0]
            if C[i] > co:
                lowest = min(C[ci:i+1])
                bottom = 0.0; k = ci - 1
                while k >= 0 and C[k] > O[k]:
                    bottom = O[k]; k -= 1
                d = co - bottom
                if d != 0 and (co - lowest) / d > s.tol:
                    s.bull = []; return True
                s.bull.pop(0)
            else:
                break
        return False


class Basket:
    def __init__(s, side):
        s.side = side; s._pos = []   # [dir, vol, price, swap]
        s.step = 0; s.unhedged = 0.0; s.extras = 0
        s.dirty = True; s.Vb = s.Sb = s.Vs = s.Ss = s.Sw = 0.0

    @property
    def pos(s):
        return s._pos

    @pos.setter
    def pos(s, v):
        s._pos = v; s.dirty = True

    def touch(s):
        s.dirty = True

    def _sums(s):
        Vb = Sb = Vs = Ss = Sw = 0.0
        for d, v, px, sw in s._pos:
            if d > 0: Vb += v; Sb += v * px
            else: Vs += v; Ss += v * px
            Sw += sw
        s.Vb, s.Sb, s.Vs, s.Ss, s.Sw = Vb, Sb, Vs, Ss, Sw
        s.dirty = False

    def volume(s):
        if s.dirty: s._sums()
        return s.Vb + s.Vs

    def profit(s, bid, ask):
        if s.dirty: s._sums()
        return (bid * s.Vb - s.Sb + s.Ss - ask * s.Vs) * CONTRACT + s.Sw


def run(P, data, verbose=False):
    m3, htf, ticks = data[P.get('SignalTF', 'm3')], data[P['FilterTF']], data['ticks']
    t3 = m3['time'].astype(np.int64)
    sig = Supertrend(m3, mult=P.get('STMult', 3.0)) if P.get('StateSource') == 'st' else Atr(P, m3['close'], sma_atr(m3, P['ATRPeriod']), sma_atr(m3, P['ATRPeriod2']))
    th = htf['time'].astype(np.int64)
    htfa = Atr(P, htf['close'], sma_atr(htf, P['ATRPeriod']), sma_atr(htf, P['ATRPeriod2']))
    cisd = Cisd(P['CISDTolerance'])
    tfsec = 180
    hsec = int(th[1] - th[0]) if P['FilterTF'] == 'm15' else 0
    hsec = 900

    start = np.datetime64(P['Start']).astype('datetime64[s]').astype(np.int64)
    end = np.datetime64(P['End']).astype('datetime64[s]').astype(np.int64)
    tk = ticks[(ticks[:, 0] >= start * 1000) & (ticks[:, 0] < end * 1000)]

    balance = P['Deposit']; closes = []; log = []; daily = []; maxVol = [0.0, 0]
    minEq = balance; maxDD = 0.0; peakEq = balance
    baskets = {-1: Basket(-1), 1: Basket(1)}

    # warmup: bars fully closed before the first tick
    first = tk[0, 0] / 1000
    last_sig = np.searchsorted(t3, first, side='right') - 2     # last closed bar index
    w0 = max(0, last_sig - P['WarmupBars'] + 1)
    for i in range(w0, last_sig + 1):
        sig.step(i); cisd.step(m3['open'][i], m3['close'][i])
    last_h = np.searchsorted(th, first, side='right') - 2
    if P['UseHTFFilter']:
        for i in range(max(0, last_h - P['WarmupBars'] + 1), last_h + 1):
            htfa.step(i)

    week_open = None; prev_tick_t = None

    def ts(t): return str(np.datetime64(int(t), 's')).replace('T', ' ')

    def in_windows(now, wins):
        mod = (int(now) % 86400) // 60
        for a, b in wins:
            if (a <= mod < b) if a <= b else (mod >= a or mod < b): return True
        return False

    def block_reason(side, now, bid, ask, kind='add'):
        if P['UseHTFFilter'] and htfa.conf != side: return 'htf'
        if kind == 'start' and in_windows(now, P.get('StartOnlyWindows', [])): return 'prewindow'
        mod = (int(now) % 86400) // 60
        for a, b in P['NoTradeWindows']:
            if (a <= mod < b) if a <= b else (mod >= a or mod < b): return 'window'
        if P['WeekOpenBlockMinutes'] > 0 and week_open is not None and now - week_open < P['WeekOpenBlockMinutes'] * 60:
            return 'weekopen'
        if P['MaxSpreadPrice'] > 0 and ask - bid > P['MaxSpreadPrice']: return 'spread'
        return ''

    def cap_blocks(b, vol):
        cap = P.get('MaxBasketLots', 0)
        return cap > 0 and b.volume() + vol > cap + 1e-9

    def open_(b, d, vol, tag, bid, ask, now):
        vol = round(vol, 2)
        px = ask if d > 0 else bid
        b.pos.append([d, vol, px, 0.0]); b.touch()
        if verbose: log.append(f"{ts(now)} {'SELL' if b.side<0 else 'BUY'} basket: {'BUY' if d>0 else 'SELL'} {vol:.2f} ({tag}) @ {px:.3f}")

    def close_all(b, reason, bid, ask, now):
        nonlocal balance
        p = b.profit(bid, ask); balance += p
        closes.append((ts(now), b.side, reason, round(p, 2), b.step))
        if verbose: log.append(f"{ts(now)} {'SELL' if b.side<0 else 'BUY'} basket: CLOSE ALL ({reason}) profit={p:.2f} step={b.step}")
        b.pos = []; b.step = 0; b.unhedged = 0.0; b.extras = 0

    def on_bar_flipgrid(b, flip, cs, bid, ask, now):
        nonlocal balance
        side = b.side
        active = sig.conf == side
        if flip == -side:                       # structure turned against: hedge everything unhedged
            if b.unhedged > 0:
                open_(b, -side, b.unhedged, 'H', bid, ask, now); b.unhedged = 0.0
            return
        if flip == side:                        # structure turned our way
            b.extras = 0                        # new structure: reset the add counter
            if b.pos:
                if P.get('ReleaseOnFlip'):
                    rel = 0.0; keep = []
                    for p in b.pos:
                        if p[0] == -side:
                            balance += ((bid - p[2]) if p[0] > 0 else (p[2] - ask)) * p[1] * CONTRACT + p[3]; rel += p[1]
                        else:
                            keep.append(p)
                    b.pos = keep; b.unhedged += rel
                return
            if block_reason(side, now, bid, ask): return
            open_(b, side, P['LotUnit'], 'L1f', bid, ask, now); b.step = 1; b.unhedged = P['LotUnit']
            return
        if cs == side and active:               # every with-side CISD adds a same-size leg
            if P.get('MaxAdds', -1) >= 0 and b.extras >= P['MaxAdds']: return
            if block_reason(side, now, bid, ask): return
            b.extras += 1
            open_(b, side, P['LotUnit'], 'A', bid, ask, now)
            b.step = max(b.step, 1); b.unhedged += P['LotUnit']

    def on_bar(b, flip, cs, bid, ask, now):
        if P.get('Mode') == 'flipgrid':
            return on_bar_flipgrid(b, flip, cs, bid, ask, now)
        side = b.side
        if not P.get('UseFlips', True): flip = 0
        active = (sig.conf == side) if P.get('UseState', True) else True
        fw, fa, cw, ca = flip == side, flip == -side, cs == side, cs == -side
        if b.step == 0:
            if active and (fw or cw):
                if block_reason(side, now, bid, ask, 'start'): return
                if cap_blocks(b, P['LotUnit']): return
                open_(b, side, P['LotUnit'], 'L1', bid, ask, now)
                b.step = 1; b.unhedged = P['LotUnit']; b.extras = 0
            return
        if b.step >= P['MaxLadderStep'] and (ca or fa):
            close_all(b, 'cap', bid, ask, now); return
        if (active and ca) or fa:
            if b.unhedged > 0:
                open_(b, -side, b.unhedged, f"H{b.step}", bid, ask, now)
                b.unhedged = 0.0; b.extras = 0
            return
        if not active or not cw: return
        if P['HedgeRelease'] and b.unhedged <= 0 and any(p[0] == -side for p in b.pos):
            if P.get('NoReleaseInWindows') and in_windows(now, P['NoTradeWindows']): return
            nonlocal balance
            rel = 0.0
            keep = []
            for p in b.pos:
                if p[0] == -side:
                    pl = ((bid - p[2]) if p[0] > 0 else (p[2] - ask)) * p[1] * CONTRACT + p[3]
                    balance += pl; rel += p[1]
                else:
                    keep.append(p)
            b.pos = keep; b.unhedged += rel; b.extras = 0
            if verbose: log.append(f"{ts(now)} release {rel:.2f}")
            return
        if b.step >= P['MaxLadderStep']: return
        if block_reason(side, now, bid, ask): return
        if b.unhedged > 0:
            if P['MaxExtraLegs'] >= 0 and b.extras >= P['MaxExtraLegs']: return
            if cap_blocks(b, P['LotUnit']): return
            open_(b, side, P['LotUnit'], f"X{b.step}", bid, ask, now)
            b.unhedged += P['LotUnit']; b.extras += 1
        else:
            nxt = b.step + 1
            if cap_blocks(b, P['LotUnit'] * nxt): return
            open_(b, side, P['LotUnit'] * nxt, f"L{nxt}", bid, ask, now)
            b.step = nxt; b.unhedged = P['LotUnit'] * nxt; b.extras = 0

    def check(b, bid, ask, now):
        if b.step == 0: return
        el = P.get('EmergencyLots', 0)
        if el > 0 and b.volume() >= el - 1e-9:
            close_all(b, 'emergency', bid, ask, now); return
        p = b.profit(bid, ask)
        if P['BasketMaxLossMoney'] > 0 and p <= -P['BasketMaxLossMoney']:
            close_all(b, 'losslimit', bid, ask, now); return
        if len(b.pos) == 1 and b.step == 1:
            d, v, px, sw = b.pos[0]
            move = (bid - px) if d > 0 else (px - ask)
            if move >= P['FirstTargetPrice']: close_all(b, 'first', bid, ask, now)
            return
        tgt = P['BreakevenMoney'] if b.step >= P['BreakevenFromStep'] else P['BasketTargetMoney']
        if p >= tgt: close_all(b, 'target', bid, ask, now)

    n3 = len(t3); nh = len(th)
    for k in range(len(tk)):
        now_ms, bid, ask = tk[k]
        now = now_ms / 1000.0
        if prev_tick_t is not None and now - prev_tick_t > 24 * 3600:
            week_open = now
        if prev_tick_t is not None and int(now // 86400) != int(prev_tick_t // 86400):
            prev_day = int(prev_tick_t // 86400)
            daily.append((ts(prev_tick_t)[:10], round(balance, 2), round(balance + baskets[-1].profit(bid, ask) + baskets[1].profit(bid, ask), 2)))
            mult = 3 if (prev_day + 3) % 7 == 2 else 1   # 1970-01-01 was Thursday; weekday 2 = Wednesday
            for bb in baskets.values():
                for p in bb.pos:
                    p[3] += (SWAP_LONG_PER_LOT if p[0] > 0 else SWAP_SHORT_PER_LOT) * p[1] * mult
                bb.touch()
        prev_tick_t = now
        # HTF bars closed
        if P['UseHTFFilter']:
            while last_h + 2 < nh and th[last_h + 2] <= now:
                last_h += 1; htfa.step(last_h)
        # SignalTF bars closed: bar i is closed once bar i+1 has started (a tick in it)
        newbars = []
        while last_sig + 2 < n3 and t3[last_sig + 2] <= now:
            last_sig += 1
            f = sig.step(last_sig); c = cisd.step(m3['open'][last_sig], m3['close'][last_sig])
            newbars.append((f, c))
        if newbars:
            f, c = newbars[-1]
            on_bar(baskets[-1], f, c, bid, ask, now)
            on_bar(baskets[1], f, c, bid, ask, now)
        check(baskets[-1], bid, ask, now)
        check(baskets[1], bid, ask, now)
        vol = baskets[-1].volume() + baskets[1].volume()
        if vol > maxVol[0]: maxVol[0] = vol; maxVol[1] = now
        if k % 20 == 0:
            eq = balance + baskets[-1].profit(bid, ask) + baskets[1].profit(bid, ask)
            peakEq = max(peakEq, eq); maxDD = max(maxDD, peakEq - eq); minEq = min(minEq, eq)

    bid, ask = tk[-1, 1], tk[-1, 2]
    for s in (-1, 1):
        if baskets[s].pos: close_all(baskets[s], 'end', bid, ask, tk[-1, 0] / 1000)
    daily.append((ts(tk[-1, 0] / 1000)[:10], round(balance, 2), round(balance, 2)))
    return dict(maxVol=(round(maxVol[0], 2), ts(maxVol[1]) if maxVol[1] else ''), daily=daily, final=round(balance, 2), maxDD=round(maxDD, 2), minEq=round(minEq, 2), closes=closes, log=log)


def load():
    return dict(m3=np.load('m3.npy'), m3t=np.load('m3t.npy'), m5=np.load('m5.npy'), m15=np.load('m15.npy'), ticks=np.load('ticks.npy'))


def summarize(name, r):
    cl = r['closes']
    from collections import defaultdict
    by = defaultdict(lambda: [0, 0.0])
    for c in cl:
        by[c[2]][0] += 1; by[c[2]][1] += c[3]
    wins = sum(1 for c in cl if c[3] > 0)
    parts = ' '.join(f"{k}:{v[0]}/{v[1]:.0f}" for k, v in sorted(by.items()))
    print(f"{name:28s} final={r['final']:9.2f} maxDD={r['maxDD']:8.2f} minEq={r['minEq']:8.2f} baskets={len(cl):4d} win={wins} | {parts}")


if __name__ == '__main__':
    data = load()
    P = dict(DEFAULTS); P.update(json.loads(sys.argv[1]) if len(sys.argv) > 1 else {})
    r = run(P, data, verbose=True)
    summarize('run', r)
