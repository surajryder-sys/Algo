"""Shared configuration for V6-Sentinel's data/bridge layer, loaded from
environment variables (.env). Fully independent of every other bot in
this repo -- own V6S_-prefixed env vars, own state files.

Multi-instrument from day one (see v6_sentinel/__init__.py): ACTIVE_SYMBOLS
lists every symbol this process currently serves, and state_file_for()
gives each symbol its own state file per component (confirmed with the
user 2026-09-18: one file per symbol, not one shared file keyed
internally) -- e.g. v6s_reversal_manager_state_XAUUSD.json. Only XAUUSD is
active today; adding a symbol later means appending to ACTIVE_SYMBOLS
plus any per-symbol tuning that component needs, not rewriting the
component itself.

This module only covers what the data/bridge layer (rates/bridge/
flip_state/bridge_flip/cisd_bridge) needs --
magic numbers, lot sizes, SL/trailing thresholds etc. belong to each
entry/execution component's own config, added when that component is
ported.

Safety: no order is ever sent from this layer -- it's read-only signal
computation. Trading-enable flags belong to the components built later
that actually place orders.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

# Every symbol V6-Sentinel currently serves. XAUUSD only for now --
# extending this list is the intended way to add an instrument later.
ACTIVE_SYMBOLS: list[str] = [
    s.strip() for s in os.getenv("V6S_SYMBOLS", "XAUUSD").split(",") if s.strip()
]

POLL_SECONDS: float = float(os.getenv("V6S_POLL_SECONDS", "1"))

MT5_TERMINAL_PATH: str | None = os.getenv("MT5_TERMINAL_PATH") or None
_login_raw = os.getenv("MT5_LOGIN", "").strip()
MT5_LOGIN: int | None = int(_login_raw) if _login_raw else None
MT5_PASSWORD: str | None = os.getenv("MT5_PASSWORD") or None
MT5_SERVER: str | None = os.getenv("MT5_SERVER") or None


def state_file_for(component: str, symbol: str, ext: str = "json") -> str:
    """Per-symbol state file name for a given component, e.g.
    state_file_for("reversal_manager_state", "XAUUSD") ->
    "v6s_reversal_manager_state_XAUUSD.json", or
    state_file_for("rm_str_decision_log", "XAUUSD", ext="jsonl") for an
    append-only log. Every stateful V6S component should derive its file
    path this way instead of taking one fixed path, so each symbol's own
    instance never shares a file with another's."""
    return f"v6s_{component}_{symbol}.{ext}"
