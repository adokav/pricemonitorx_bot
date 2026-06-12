"""Saf teknik gösterge fonksiyonları — bağımlılıksız.

Hepsi `list[float]` alır; warm-up için seri başında `None` döner ya da yeterli
veri yoksa `None` verir. Dış bağımlılık yok (numpy/pandas gerekmez), bu yüzden
birim testleri determinist ve hızlıdır.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

Number = float
Series = List[Optional[float]]


def sma(values: List[Number], period: int) -> Optional[float]:
    """Son `period` değerin basit hareketli ortalaması."""
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def sma_series(values: List[Number], period: int) -> Series:
    """Her nokta için SMA (warm-up = None)."""
    out: Series = []
    if period <= 0:
        return [None] * len(values)
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        out.append(running / period if i + 1 >= period else None)
    return out


def ema_series(values: List[Number], period: int) -> Series:
    """SMA tohumlu üstel hareketli ortalama serisi."""
    out: Series = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def ema(values: List[Number], period: int) -> Optional[float]:
    series = ema_series(values, period)
    return series[-1] if series else None


def rsi(values: List[Number], period: int = 14) -> Optional[float]:
    """Wilder yumuşatmalı RSI (0-100)."""
    if len(values) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def macd(
    values: List[Number],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[Series, Series, Series]:
    """MACD çizgisi, sinyal çizgisi ve histogram serilerini döner."""
    ema_fast = ema_series(values, fast)
    ema_slow = ema_series(values, slow)
    macd_line: Series = [
        (a - b) if (a is not None and b is not None) else None
        for a, b in zip(ema_fast, ema_slow)
    ]
    macd_vals = [m for m in macd_line if m is not None]
    sig_vals = ema_series(macd_vals, signal)
    signal_line: Series = [None] * len(values)
    offset = len(values) - len(macd_vals)
    for i, s in enumerate(sig_vals):
        signal_line[offset + i] = s
    hist: Series = [
        (m - s) if (m is not None and s is not None) else None
        for m, s in zip(macd_line, signal_line)
    ]
    return macd_line, signal_line, hist


def true_range(
    highs: List[Number], lows: List[Number], closes: List[Number]
) -> List[float]:
    trs: List[float] = []
    for i in range(len(closes)):
        if i == 0:
            trs.append(highs[i] - lows[i])
        else:
            prev_close = closes[i - 1]
            trs.append(
                max(
                    highs[i] - lows[i],
                    abs(highs[i] - prev_close),
                    abs(lows[i] - prev_close),
                )
            )
    return trs


def atr(
    highs: List[Number],
    lows: List[Number],
    closes: List[Number],
    period: int = 14,
) -> Optional[float]:
    """Wilder ATR — veri-bazlı stop için kullanılır."""
    trs = true_range(highs, lows, closes)
    if len(trs) < period:
        return None
    value = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        value = (value * (period - 1) + trs[i]) / period
    return value


def last(series: Series) -> Optional[float]:
    for v in reversed(series):
        if v is not None:
            return v
    return None
