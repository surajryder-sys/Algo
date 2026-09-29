"""Dynamic Zones on GAP-DELIMITED sessions, exactly as mql5/Dynamic_Zones_CISD_MajorMinor.mq5 (and EA v2.13):
a new session starts wherever consecutive H1 bars are more than one hour apart; anchor = the session's first
H1 open; half ranges = average High-Low of the previous 5 / 10 COMPLETE sessions."""
import numpy as np
from v6sim import agg

def session_zones(m1, short=5, long_=10):
    h = agg(m1, 3600)
    starts = np.concatenate([[0], np.flatnonzero(np.diff(h['time']) > 3600) + 1])
    ends = np.concatenate([starts[1:], [len(h)]])
    hi = np.maximum.reduceat(h['high'], starts); lo = np.minimum.reduceat(h['low'], starts)
    rng = hi - lo; t0 = h['time'][starts]; op = h['open'][starts]
    n = len(starts); Z = np.full((n, 4), np.nan)
    m = max(short, long_)
    for k in range(m, n):
        h5 = rng[k-short:k].mean() / 2.0; h10 = rng[k-long_:k].mean() / 2.0
        Z[k] = (op[k] + h5, op[k] + h10, op[k] - h5, op[k] - h10)
    return t0, Z

def session_hl(m1):
    """Start time, high and low of every gap-delimited session."""
    h = agg(m1, 3600)
    starts = np.concatenate([[0], np.flatnonzero(np.diff(h['time']) > 3600) + 1])
    return h['time'][starts], np.maximum.reduceat(h['high'], starts), np.minimum.reduceat(h['low'], starts)
