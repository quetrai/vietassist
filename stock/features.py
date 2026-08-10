from __future__ import annotations

from dataclasses import dataclass

from stock.market import Series


@dataclass(frozen=True)
class Features:
    price: float
    change_pct: float
    sma20: float
    sma50: float
    rsi14: float
    volatility_pct: float
    volume_ratio: float
    relative_strength_20d: float
    support: float
    resistance: float
    avg_volume_20d: float = 0.0


def sma(values: list[float], period: int) -> float:
    if len(values) < period:
        raise ValueError("Không đủ dữ liệu SMA")
    return sum(values[-period:]) / period


def rsi(values: list[float], period: int = 14) -> float:
    changes = [b - a for a, b in zip(values[-period - 1 : -1], values[-period:], strict=True)]
    gains = sum(max(value, 0) for value in changes) / period
    losses = sum(max(-value, 0) for value in changes) / period
    if losses == 0:
        return 100.0
    return 100 - 100 / (1 + gains / losses)


def pct_change(values: list[float], periods: int) -> float:
    start, end = values[-periods - 1], values[-1]
    return (end / start - 1) * 100 if start else 0.0


def calculate(series: Series, market: Series) -> Features:
    returns = [
        (b / a - 1) * 100
        for a, b in zip(series.closes[-21:-1], series.closes[-20:], strict=True)
        if a
    ]
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / max(1, len(returns) - 1)
    avg_volume = sum(series.volumes[-21:-1]) / 20
    own_20d = pct_change(series.closes, 20)
    market_20d = pct_change(market.closes, 20)
    return Features(
        price=series.price,
        change_pct=pct_change(series.closes, 1),
        sma20=sma(series.closes, 20),
        sma50=sma(series.closes, 50),
        rsi14=rsi(series.closes),
        volatility_pct=variance**0.5,
        volume_ratio=series.volumes[-1] / avg_volume if avg_volume else 0.0,
        relative_strength_20d=own_20d - market_20d,
        support=min(series.lows[-20:]),
        resistance=max(series.highs[-20:]),
        avg_volume_20d=avg_volume,
    )
