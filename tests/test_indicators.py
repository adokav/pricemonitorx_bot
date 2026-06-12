"""Saf gösterge birim testleri — ağ/bağımlılık gerekmez."""
import math

from crypto_signals import indicators as ind


def test_sma_basic():
    assert ind.sma([1, 2, 3, 4, 5], 5) == 3.0
    assert ind.sma([1, 2, 3, 4, 5], 2) == 4.5
    assert ind.sma([1, 2], 5) is None


def test_sma_series_warmup():
    series = ind.sma_series([1, 2, 3, 4], 2)
    assert series[0] is None
    assert series[1] == 1.5
    assert series[2] == 2.5
    assert series[3] == 3.5


def test_ema_matches_sma_for_constant_series():
    values = [10.0] * 30
    assert math.isclose(ind.ema(values, 10), 10.0, rel_tol=1e-9)


def test_rsi_all_gains_is_100():
    values = list(range(1, 30))  # sürekli artan
    assert ind.rsi(values, 14) == 100.0


def test_rsi_all_losses_is_0():
    values = list(range(30, 1, -1))  # sürekli azalan
    assert ind.rsi(values, 14) == 0.0


def test_rsi_bounded():
    values = [1, 2, 1, 3, 2, 4, 3, 5, 4, 6, 5, 7, 6, 8, 7, 9]
    value = ind.rsi(values, 14)
    assert value is not None and 0.0 <= value <= 100.0


def test_macd_shapes():
    values = [float(i) for i in range(60)]
    macd_line, signal_line, hist = ind.macd(values)
    assert len(macd_line) == len(signal_line) == len(hist) == 60
    # Sürekli artan seride MACD pozitif olmalı
    assert ind.last(macd_line) > 0


def test_atr_positive():
    highs = [float(i + 2) for i in range(30)]
    lows = [float(i) for i in range(30)]
    closes = [float(i + 1) for i in range(30)]
    value = ind.atr(highs, lows, closes, 14)
    assert value is not None and value > 0
