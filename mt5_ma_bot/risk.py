"""Position sizing and SL/TP price calculation from risk % of account equity."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import MetaTrader5 as mt5

from mt5_ma_bot.config import Config

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrderPlan:
    volume: float
    sl: float
    tp: float
    pip_size: float


def _pip_size(symbol_info) -> float:
    # 5- and 3-digit brokers quote fractional pips; the pip is 10x the point.
    return symbol_info.point * 10 if symbol_info.digits in (3, 5) else symbol_info.point


def _round_to_step(volume: float, step: float, min_vol: float, max_vol: float) -> float:
    steps = math.floor(volume / step)
    rounded = steps * step
    rounded = max(min_vol, min(max_vol, rounded))
    return round(rounded, 8)


def _pip_value_per_lot(symbol_info, pip_size: float) -> float:
    if symbol_info.trade_tick_size <= 0:
        raise RuntimeError(f"Invalid trade_tick_size for {symbol_info.name}")
    return symbol_info.trade_tick_value * (pip_size / symbol_info.trade_tick_size)


def build_order_plan(cfg: Config, symbol: str, side: str, entry_price: float) -> OrderPlan:
    account = mt5.account_info()
    if account is None:
        code, desc = mt5.last_error()
        raise RuntimeError(f"Could not read account info: [{code}] {desc}")

    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        raise RuntimeError(f"Symbol info unavailable for {symbol}")

    pip_size = _pip_size(symbol_info)
    pip_value_per_lot = _pip_value_per_lot(symbol_info, pip_size)
    if pip_value_per_lot <= 0:
        raise RuntimeError(f"Computed non-positive pip value for {symbol}; check symbol/account currency")

    risk_amount = account.balance * (cfg.risk_percent / 100.0)
    raw_volume = risk_amount / (cfg.stop_loss_pips * pip_value_per_lot)

    volume = _round_to_step(
        raw_volume,
        symbol_info.volume_step,
        symbol_info.volume_min,
        symbol_info.volume_max,
    )
    if volume < symbol_info.volume_min:
        raise RuntimeError(
            f"Computed volume {raw_volume:.4f} lots is below the broker minimum "
            f"{symbol_info.volume_min} lots for {symbol}. Reduce STOP_LOSS_PIPS or increase RISK_PERCENT."
        )

    sl_distance = cfg.stop_loss_pips * pip_size
    tp_distance = cfg.take_profit_pips * pip_size

    if side == "BUY":
        sl = entry_price - sl_distance
        tp = entry_price + tp_distance
    else:
        sl = entry_price + sl_distance
        tp = entry_price - tp_distance

    sl = round(sl, symbol_info.digits)
    tp = round(tp, symbol_info.digits)

    log.info(
        "Sized order for %s %s: balance=%.2f risk=%.2f%% (%.2f) sl_pips=%.1f -> volume=%.2f lots",
        symbol, side, account.balance, cfg.risk_percent, risk_amount, cfg.stop_loss_pips, volume,
    )

    return OrderPlan(volume=volume, sl=sl, tp=tp, pip_size=pip_size)
