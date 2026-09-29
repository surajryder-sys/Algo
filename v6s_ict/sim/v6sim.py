"""Python mirror of v6s_ict/V6S_ICT_EA.mq5 (tick-level execution, closed-bar signals)."""
import numpy as np

EMPTY = 1.7976931348623157e308


class MajorMinor:
    """Straight translation of CMajorMinor (itself verbatim from the indicator)."""
    def __init__(self, name, PP=5):
        self.name = name; self.PP = PP
        self.hi = []; self.lo = []; self.cl = []; self.tm = []; self.n = 0
        self.T = []; self.V = []; self.I = []; self.TA = []; self.VA = []; self.IA = []
        self.MHL = 0.0; self.MLL = 0.0; self.MHI = -1; self.MLI = -1; self.MHT = ''; self.MLT = ''
        self.MLSet = False; self.L0 = True; self.L1 = True
        self.LHV = 0.0; self.LLV = 0.0; self.LHI = -1; self.LLI = -1
        self.MajSupX = self.MajResX = self.MinSupX = self.MinResX = -1
        self.MajSupY = self.MajResY = self.MinSupY = self.MinResY = 0.0

    def piv_hi(self, c):
        PP = self.PP
        if c - PP < 0 or c + PP >= self.n: return False
        v = self.hi[c]
        for k in range(c - PP, c + PP + 1):
            if k != c and self.hi[k] >= v: return False
        return True

    def piv_lo(self, c):
        PP = self.PP
        if c - PP < 0 or c + PP >= self.n: return False
        v = self.lo[c]
        for k in range(c - PP, c + PP + 1):
            if k != c and self.lo[k] <= v: return False
        return True

    def push_h(self):
        N = len(self.T); t = ('HH' if self.V[N-2] < self.LHV else 'LH') if N > 2 else 'H'
        self.T.append(t); self.V.append(self.LHV); self.I.append(self.LHI)

    def push_l(self):
        N = len(self.T); t = ('HL' if self.V[N-2] < self.LLV else 'LL') if N > 2 else 'L'
        self.T.append(t); self.V.append(self.LLV); self.I.append(self.LLI)

    def rep_h(self):
        self.T.pop(); self.V.pop(); self.I.pop(); self.push_h()

    def rep_l(self):
        self.T.pop(); self.V.pop(); self.I.pop(); self.push_l()

    def classify(self, hh, hl, c):
        N = len(self.T); T = self.T; V = self.V
        if hh and hl:
            if N == 0: return
            last = T[N-1]
            if last in ('L', 'LL'):
                self.rep_l() if self.LLV < V[N-1] else self.push_h()
            elif last in ('H', 'HH'):
                self.rep_h() if self.LHV > V[N-1] else self.push_l()
            elif last == 'LH':
                if self.LHV < V[N-1]: self.push_l()
                elif self.LHV > V[N-1]:
                    if c < V[N-1]: self.rep_h()
                    elif c > V[N-1]: self.push_l()
            elif last == 'HL':
                if self.LLV > V[N-1]: self.push_h()
                elif self.LLV < V[N-1]:
                    if c > V[N-1]: self.rep_l()
                    elif c < V[N-1]: self.push_h()
        elif hh:
            if N == 0:
                T.insert(0, 'H'); V.insert(0, self.LHV); self.I.insert(0, self.LHI)
            else:
                last = T[N-1]
                if last in ('L', 'HL', 'LL'):
                    if self.LHV > V[N-1]: self.push_h()
                    elif self.LHV < V[N-1]: self.rep_l()
                elif last in ('H', 'HH', 'LH'):
                    if V[N-1] < self.LHV: self.rep_h()
        elif hl:
            if N == 0:
                T.insert(0, 'L'); V.insert(0, self.LLV); self.I.insert(0, self.LLI)
            else:
                last = T[N-1]
                if last in ('H', 'HH', 'LH'):
                    if self.LLV < V[N-1]: self.push_l()
                    elif self.LLV > V[N-1]: self.rep_h()
                elif last in ('L', 'HL', 'LL'):
                    if V[N-1] > self.LLV: self.rep_l()

    def upd_major(self, c):
        TA, VA, IA, T = self.TA, self.VA, self.IA, self.T
        nA = len(VA)
        if nA <= 1: return
        nB = len(T)
        if nB < 1: return
        if c > self.MHL:
            t = TA[nA-1]
            if t == 'mL':
                TA[nA-1] = 'ML'; self.MLL = VA[nA-1]; self.MLI = IA[nA-1]; self.MLT = TA[nA-1]
            elif t in ('mHL', 'mLL'):
                TA[nA-1] = 'M' + T[nB-1]; self.MLL = VA[nA-1]; self.MLI = IA[nA-1]; self.MLT = TA[nA-1]
            elif t in ('mLH', 'mHH', 'MLH', 'MHH'):
                if nA >= 2 and nB >= 2 and TA[nA-2] in ('mHL', 'mLL'):
                    TA[nA-2] = 'M' + T[nB-2]; self.MLL = VA[nA-2]; self.MLI = IA[nA-2]; self.MLT = TA[nA-2]
        if VA[nA-1] > self.MHL:
            t = TA[nA-1]
            if t == 'mH':
                TA[nA-1] = 'MH'; self.MHL = VA[nA-1]; self.MHI = IA[nA-1]; self.MHT = TA[nA-1]
            elif t in ('mLH', 'mHH', 'MHH'):
                TA[nA-1] = 'M' + T[nB-1]; self.MHL = VA[nA-1]; self.MHI = IA[nA-1]; self.MHT = TA[nA-1]
        if c < self.MLL:
            t = TA[nA-1]
            if t == 'mH':
                TA[nA-1] = 'MH'; self.MHL = VA[nA-1]; self.MHI = IA[nA-1]; self.MHT = TA[nA-1]
            elif t in ('mLH', 'mHH'):
                TA[nA-1] = 'M' + T[nB-1]; self.MHL = VA[nA-1]; self.MHI = IA[nA-1]; self.MHT = TA[nA-1]
            elif t in ('mHL', 'mLL', 'MHL', 'MLL'):
                if nA >= 2 and nB >= 2 and TA[nA-2] in ('mLH', 'mHH'):
                    TA[nA-2] = 'M' + T[nB-2]; self.MHL = VA[nA-2]; self.MHI = IA[nA-2]; self.MHT = TA[nA-2]
        if VA[nA-1] < self.MLL:
            t = TA[nA-1]
            if t == 'mL':
                TA[nA-1] = 'ML'; self.MLL = VA[nA-1]; self.MLI = IA[nA-1]; self.MLT = TA[nA-1]
            elif t == 'mHL':
                TA[nA-1] = 'M' + T[nB-1]; self.MLL = VA[nA-1]; self.MLI = IA[nA-1]; self.MLT = TA[nA-1]
            elif t in ('mLL', 'MLL'):
                TA[nA-1] = 'M' + T[nB-1]; self.MLL = VA[nA-1]; self.MLI = IA[nA-1]; self.MLT = TA[nA-1]

    def process(self, i):
        PP = self.PP; c = i - PP; hh = hl = False
        if c >= PP and c + PP < self.n:
            hh = self.piv_hi(c); hl = self.piv_lo(c)
        if hh: self.LHV = self.hi[c]; self.LHI = c
        if hl: self.LLV = self.lo[c]; self.LLI = c
        cl = self.cl[i]
        pN = len(self.V)
        pV = self.V[pN-1] if pN > 0 else EMPTY
        pT = self.T[pN-1] if pN > 0 else ''
        self.classify(hh, hl, cl)
        T, V, I = self.T, self.V, self.I
        if len(T) == 2:
            if T[0] == 'H':
                self.MHL = V[0]; self.MLL = V[1]; self.MHI = I[0]; self.MLI = I[1]; self.MHT = T[0]; self.MLT = T[1]
            elif T[0] == 'L':
                self.MHL = V[1]; self.MLL = V[0]; self.MHI = I[1]; self.MLI = I[0]; self.MHT = T[1]; self.MLT = T[0]
            self.MLSet = True
        if len(V) == 1 and self.L0:
            self.TA.insert(0, 'M' + T[0]); self.VA.insert(0, V[0]); self.IA.insert(0, I[0]); self.L0 = False
        if len(V) == 2 and self.L1:
            self.TA.insert(1, 'M' + T[1]); self.VA.insert(1, V[1]); self.IA.insert(1, I[1]); self.L1 = False
        m = len(V)
        if m > 1:
            cv = V[m-1]; ct = T[m-1]
            if cv != pV:
                pf = pT[-1] if pT else ''
                cf = ct[-1]
                if pf != cf:
                    self.TA.append('m' + ct); self.VA.append(cv); self.IA.append(I[m-1])
                elif self.VA:
                    self.VA[-1] = cv; self.IA[-1] = I[m-1]
        if self.MLSet: self.upd_major(cl)

    def track(self):
        nA = len(self.TA)
        if nA <= 2: return
        x = self.IA[-1]; y = self.VA[-1]; t = self.TA[-1]
        if t in ('MLL', 'MHL'): self.MajSupX, self.MajSupY = x, y
        elif t in ('MHH', 'MLH'): self.MajResX, self.MajResY = x, y
        elif t in ('mLL', 'mHL'): self.MinSupX, self.MinSupY = x, y
        elif t in ('mHH', 'mLH'): self.MinResX, self.MinResY = x, y

    def add(self, h, l, c, t):
        self.hi.append(h); self.lo.append(l); self.cl.append(c); self.tm.append(t); self.n += 1
        self.process(self.n - 1); self.track()

    def levels(self):
        out = []
        if self.MajSupX >= 0: out.append(('S', round(self.MajSupY, 3), 'Maj'))
        if self.MinSupX >= 0: out.append(('S', round(self.MinSupY, 3), 'Min'))
        if self.MajResX >= 0: out.append(('R', round(self.MajResY, 3), 'Maj'))
        if self.MinResX >= 0: out.append(('R', round(self.MinResY, 3), 'Min'))
        return out


def agg(m1, sec):
    b = (m1['time'] // sec) * sec
    idx = np.flatnonzero(np.diff(b)) + 1; st = np.concatenate([[0], idx]); en = np.concatenate([idx, [len(b)]])
    d = np.dtype([('time', '<i8'), ('open', '<f8'), ('high', '<f8'), ('low', '<f8'), ('close', '<f8')])
    r = np.empty(len(st), dtype=d); r['time'] = b[st]; r['open'] = m1['open'][st]; r['close'] = m1['close'][en-1]
    r['high'] = np.maximum.reduceat(m1['high'], st); r['low'] = np.minimum.reduceat(m1['low'], st); return r


TFS = [('H4', 14400), ('H2', 7200), ('H1', 3600), ('M30', 1800), ('M15', 900), ('M10', 600), ('M5', 300), ('M3', 180)]
