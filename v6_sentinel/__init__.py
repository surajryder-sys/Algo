"""V6-Sentinel -- deliberate rebuild of V5-Sentinel's XAUUSD strategy,
porting one component at a time (verify-then-copy, discussed per
component) instead of a bulk migration. V5-Sentinel (v5_sentinel/) keeps
running untouched throughout.

Multi-instrument from day one: every component takes symbol as an
explicit parameter/config key rather than hardcoding one, so adding a
second instrument later means adding config, not touching component
logic (see config.py's ACTIVE_SYMBOLS). The first working build still
targets XAUUSD only -- other symbols are added deliberately, one at a
time, once tuned.

Fully independent of every other lineage in this repo (algo_v2/, v3/,
v4/, v5_sentinel/) -- no shared imports either direction, matching the
pattern those lineages already follow with each other.
"""
