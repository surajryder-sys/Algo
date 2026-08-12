"""Parses TradingView's Data Window panel text (as returned by reading the
page's rendered text) into structured ATR-trail and OB-zone data.

The Data Window renders each exposed plot as two consecutive lines: a label
line, then its current numeric value on the next line (formatted with comma
thousands separators, e.g. "4,053.724"). This module only depends on that
label/value line-pairing -- it doesn't care about anything else on the page.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_NUMBER_RE = re.compile(r"^-?[\d,]+(\.\d+)?$")
# Matches the ticker in a Data Window header like "XAUUSD · 5 · OANDA" or
# "BTCUSD · 1h · Bitstamp" (symbol, then timeframe token, then exchange).
# Timeframe is \w+ (not just digits) -- TradingView shows hour/day/week
# timeframes as "1h"/"4h"/"D"/"W", not pure numbers, and a digits-only
# pattern silently failed to match those, mislabeling data with whatever
# symbol/timeframe was last configured instead. Lets the scraper label
# output with whatever's actually on screen instead of blindly trusting its
# configured symbol (which would also mislabel data if the chart gets
# manually switched to a different symbol, e.g. for testing while XAUUSD's
# market is closed on weekends).
_SYMBOL_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9})\s*·\s*(\w+)\s*·\s*[A-Za-z]")

_ATR_LABELS = {"Trailing Stop": "trail_stop", "Trend": "trend"}
# The symbol's own live Close, from the Data Window's own O/H/L/C block (not
# an indicator plot) -- used as a stand-in for "price at detection", the
# same live-price role ob_detector_webhook.pine's `close` plays in the
# alert payload (see that script's detected_price fix). Without this,
# _apply_direction had no live price to use and fell back to a zone's own
# edge -- same structural bug as the pre-fix Pine script (distance always
# negative, M3/M5 entries always NONE).
_PRICE_LABELS = {"Close": "close"}
_ZONE_LABELS = {
    f"{direction}{n} {field}": (direction.lower(), n, field.lower())
    for direction in ("Bull", "Bear")
    for n in (1, 2, 3, 4)  # matches this indicator's deployed bull/bear_ext_last=4
    for field in ("Top", "Btm", "Retested")  # no Start -- see ob_detector_webhook.pine.
    # Retested is Pine's own wick-based check (1/0), authoritative over
    # tv_scraper's own live-Close approximation in _apply_direction --
    # see that indicator's mark_retests()/"Retested" Data Window plots.
}


def _to_number(raw: str) -> Optional[float]:
    raw = raw.strip()
    if not _NUMBER_RE.match(raw):
        return None  # "n/a" or similar -- zone slot not in use
    return float(raw.replace(",", ""))


@dataclass
class ParsedState:
    atr: Optional[dict]  # {"trail_stop": float, "trend": int} or None
    bull_zones: list[dict]  # [{"top", "btm"}, ...] newest first -- no
    bear_zones: list[dict]  # start_time (see ob_detector_webhook.pine)
    symbol: Optional[str]  # actual ticker read off the chart, e.g. "XAUUSD"
    timeframe: Optional[str]  # actual timeframe read off the chart, e.g. "5"
    close: Optional[float]  # live Close from the Data Window's O/H/L/C block


def parse_data_window(text: str) -> ParsedState:
    lines = [ln.strip() for ln in text.splitlines()]

    atr_fields: dict = {}
    zone_fields: dict[tuple[str, int], dict] = {}
    price_fields: dict = {}

    i = 0
    while i < len(lines) - 1:
        label = lines[i]
        value_line = lines[i + 1]

        if label in _ATR_LABELS:
            value = _to_number(value_line)
            if value is not None:
                atr_fields[_ATR_LABELS[label]] = value
            i += 2
            continue

        if label in _PRICE_LABELS:
            value = _to_number(value_line)
            if value is not None:
                price_fields[_PRICE_LABELS[label]] = value
            i += 2
            continue

        if label in _ZONE_LABELS:
            direction, n, field = _ZONE_LABELS[label]
            value = _to_number(value_line)
            if value is not None:
                key = (direction, n)
                zone_fields.setdefault(key, {})[field] = value
            i += 2
            continue

        i += 1

    atr = None
    if "trail_stop" in atr_fields:
        atr = {
            "trail_stop": atr_fields["trail_stop"],
            "trend": int(atr_fields.get("trend", 0)),
        }

    def _zones(direction: str) -> list[dict]:
        out = []
        for n in (1, 2, 3, 4):
            f = zone_fields.get((direction, n))
            if f and "top" in f and "btm" in f:
                zone = {"top": f["top"], "btm": f["btm"]}
                # None (not this bool's normal 0.0/1.0) if the indicator on
                # this pane doesn't have the Retested plots yet -- lets
                # _apply_direction fall back to its own live-Close check
                # rather than silently treating "missing" as "not retested".
                if "retested" in f:
                    zone["retested"] = f["retested"] > 0.5
                out.append(zone)
        return out

    symbol_match = _SYMBOL_RE.search(text)
    symbol = symbol_match.group(1) if symbol_match else None
    timeframe = symbol_match.group(2) if symbol_match else None

    return ParsedState(atr=atr, bull_zones=_zones("bull"), bear_zones=_zones("bear"),
                        symbol=symbol, timeframe=timeframe, close=price_fields.get("close"))
