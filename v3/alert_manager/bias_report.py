"""Builds the condensed, mobile-friendly bias/zone summary sent in reply
to a Telegram `/bias <SYMBOL>` command -- the on-demand counterpart to
the push alerts watcher.py already sends. Deliberately NOT the full
per-timeframe table (40+ rows) used in chat reports -- unreadable on a
phone screen. Shows only: current price, nearest untested resistance/
support across ALL tracked timeframes combined, and anything virgin
formed very recently (likely still relevant regardless of distance).

The "Bias" line is intentionally just the factual distance/recency
picture, not an attempt to replicate a human's full nuanced read (mixing
multi-timeframe confluence, momentum, etc.) -- that kind of judgment
call is better left to actually asking, not a fixed formula pretending
sophistication it doesn't have.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Optional

_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
_TF_LABELS = {"240": "H4", "120": "H2", "60": "H1", "30": "M30", "15": "M15",
              "5": "M5", "3": "M3", "1": "M1"}
_RECENT_WINDOW_SECONDS = 2 * 60 * 60  # zones formed within the last 2h are shown regardless of distance
_MAX_LEVELS = 6


def _read_virgin_zones(zone_state_file: str) -> list[dict]:
    p = Path(zone_state_file)
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return []

    out = []
    for key, entries in raw.items():
        parts = key.split("|")
        if len(parts) != 3:
            continue
        _symbol, timeframe, direction = parts
        for _start_time_str, zone in entries.items():
            if not zone.get("virgin", True):
                continue
            out.append({
                "timeframe": timeframe,
                "direction": direction,
                "start_time": int(zone["start_time"]),
                "top": zone["top"],
                "btm": zone["btm"],
            })
    return out


def _fmt_time(epoch: int) -> str:
    return datetime.datetime.fromtimestamp(epoch, tz=_IST).strftime("%m-%d %H:%M IST")


def build_report(symbol: str, zone_state_file: str, price: Optional[float]) -> str:
    now = int(datetime.datetime.now(tz=_IST).timestamp())
    zones = _read_virgin_zones(zone_state_file)

    if price is None:
        header = f"\U0001F4CA {symbol} — {_fmt_time(now)}\nPrice: unavailable (MT5 read failed)\n"
    else:
        header = f"\U0001F4CA {symbol} — {_fmt_time(now)}\nClose: {price:.2f}\n"

    if not zones:
        return header + "\nNo tracked zones found."

    inside = []
    recent = []
    above = []  # bear zones (resistance), price below them
    below = []  # bull zones (support), price above them

    for z in zones:
        if price is not None and z["btm"] <= price <= z["top"]:
            inside.append(z)
        elif now - z["start_time"] <= _RECENT_WINDOW_SECONDS:
            recent.append(z)
        elif price is not None and z["btm"] > price:
            above.append(z)
        elif price is not None and z["top"] < price:
            below.append(z)

    above.sort(key=lambda z: z["btm"])  # nearest above first
    below.sort(key=lambda z: -z["top"])  # nearest below first
    recent.sort(key=lambda z: -z["start_time"])  # most recent first

    lines = [header]

    if inside:
        lines.append("⚠️ Price is CURRENTLY INSIDE a virgin zone:")
        for z in inside:
            tf = _TF_LABELS.get(z["timeframe"], z["timeframe"])
            dot = "\U0001F7E2" if z["direction"] == "bull" else "\U0001F534"
            lines.append(f"{dot} {tf} {z['btm']:.2f}-{z['top']:.2f} (formed {_fmt_time(z['start_time'])})")
        lines.append("")

    if price is not None:
        nearest_above = above[0] if above else None
        nearest_below = below[0] if below else None
        if nearest_above:
            dist = nearest_above["btm"] - price
            lines.append(f"Nearest resistance: {nearest_above['btm']:.2f} (+{dist:.2f}, "
                         f"{_TF_LABELS.get(nearest_above['timeframe'], nearest_above['timeframe'])})")
        if nearest_below:
            dist = price - nearest_below["top"]
            lines.append(f"Nearest support: {nearest_below['top']:.2f} (-{dist:.2f}, "
                         f"{_TF_LABELS.get(nearest_below['timeframe'], nearest_below['timeframe'])})")
        lines.append("")

    shown = 0
    if recent:
        lines.append("Recently formed (last 2h):")
        for z in recent:
            if shown >= _MAX_LEVELS:
                break
            tf = _TF_LABELS.get(z["timeframe"], z["timeframe"])
            dot = "\U0001F7E2" if z["direction"] == "bull" else "\U0001F534"
            lines.append(f"{dot} {tf} {z['btm']:.2f}-{z['top']:.2f} (formed {_fmt_time(z['start_time'])})")
            shown += 1

    return "\n".join(lines).strip()
