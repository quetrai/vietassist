from __future__ import annotations

from dataclasses import dataclass
from math import sqrt

from stock.market import Series


@dataclass(frozen=True)
class MACD:
    line: float
    signal: float
    histogram: float
    crossover: str


@dataclass(frozen=True)
class Bollinger:
    upper: float
    middle: float
    lower: float
    width_pct: float
    percent_b: float
    squeeze: bool
    available: bool


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
    ema10: float = 0.0
    ema20: float = 0.0
    momentum_10d_pct: float = 0.0
    atr14: float = 0.0
    atr_pct: float = 0.0
    macd: MACD = MACD(0.0, 0.0, 0.0, "none")
    bollinger: Bollinger = Bollinger(0.0, 0.0, 0.0, 0.0, 50.0, False, False)
    above_ema10: bool = False
    above_ema20: bool = False


def sma(values: list[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"Không đủ dữ liệu SMA{period}")
    return sum(values[-period:]) / period


def ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    if len(values) < period:
        return sum(values) / len(values)
    alpha = 2 / (period + 1)
    value = sum(values[:period]) / period
    for current in values[period:]:
        value = current * alpha + value * (1 - alpha)
    return value


def rsi(values: list[float], period: int = 14) -> float:
    if len(values) < period + 1:
        raise ValueError("Không đủ dữ liệu RSI")
    gains = []
    losses = []
    for previous, current in zip(values[-period - 1 : -1], values[-period :], strict=True):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    average_gain = sum(gains) / period
    average_loss = sum(losses) / period
    if average_loss == 0:
        return 100.0
    return 100 - 100 / (1 + average_gain / average_loss)


def pct_change(values: list[float], periods: int) -> float:
    if len(values) <= periods:
        raise ValueError(f"Không đủ dữ liệu cho biến động {periods} phiên")
    start, end = values[-periods - 1], values[-1]
    return (end / start - 1) * 100 if start else 0.0


def _macd(values: list[float]) -> MACD:
    if len(values) < 35:
        return MACD(0.0, 0.0, 0.0, "none")
    alpha12 = 2 / 13
    alpha26 = 2 / 27
    ema12 = sum(values[:12]) / 12
    ema26 = sum(values[:26]) / 26
    macd_values = []
    for index, value in enumerate(values):
        if index >= 12:
            ema12 = value * alpha12 + ema12 * (1 - alpha12)
        if index >= 26:
            ema26 = value * alpha26 + ema26 * (1 - alpha26)
        if index >= 26:
            macd_values.append(ema12 - ema26)
    signal = ema(macd_values, 9)
    line = macd_values[-1]
    histogram = line - signal
    previous_signal = ema(macd_values[:-1], 9) if len(macd_values) > 9 else signal
    previous_histogram = macd_values[-2] - previous_signal if len(macd_values) > 1 else histogram
    crossover = "bullish" if previous_histogram <= 0 < histogram else "bearish" if previous_histogram >= 0 > histogram else "none"
    return MACD(round(line, 2), round(signal, 2), round(histogram, 2), crossover)


def _bollinger(values: list[float], price: float) -> Bollinger:
    if len(values) < 20:
        return Bollinger(price, price, price, 0.0, 50.0, False, False)
    window = values[-20:]
    middle = sum(window) / 20
    deviation = sqrt(sum((value - middle) ** 2 for value in window) / 20)
    upper = middle + 2 * deviation
    lower = middle - 2 * deviation
    width = ((upper - lower) / middle * 100) if middle else 0.0
    percent_b = ((price - lower) / (upper - lower) * 100) if upper != lower else 50.0
    return Bollinger(round(upper), round(middle), round(lower), round(width, 2), round(max(0, min(100, percent_b)), 1), width < 5, True)


def _atr(series: Series, period: int = 14) -> float:
    if len(series.closes) < period + 1:
        return 0.0
    true_ranges = []
    for index in range(1, len(series.closes)):
        true_ranges.append(
            max(
                series.highs[index] - series.lows[index],
                abs(series.highs[index] - series.closes[index - 1]),
                abs(series.lows[index] - series.closes[index - 1]),
            )
        )
    return sum(true_ranges[-period:]) / period


def calculate(series: Series, market: Series) -> Features:
    if len(series.closes) < 50 or len(market.closes) < 21:
        raise ValueError("Không đủ dữ liệu lịch sử để phân tích")
    returns = [
        (current / previous - 1) * 100
        for previous, current in zip(series.closes[-21:-1], series.closes[-20:], strict=True)
        if previous
    ]
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / max(1, len(returns) - 1)
    avg_volume = sum(series.volumes[-21:-1]) / 20
    atr14 = _atr(series)
    own_20d = pct_change(series.closes, 20)
    market_20d = pct_change(market.closes, 20)
    price = series.price
    ema10 = ema(series.closes, 10)
    ema20 = ema(series.closes, 20)
    return Features(
        price=price,
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
        ema10=ema10,
        ema20=ema20,
        momentum_10d_pct=pct_change(series.closes, 10),
        atr14=atr14,
        atr_pct=atr14 / price * 100 if price else 0.0,
        macd=_macd(series.closes),
        bollinger=_bollinger(series.closes, price),
        above_ema10=price > ema10,
        above_ema20=price > ema20,
    )
