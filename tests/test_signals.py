"""Sinyal motoru (kompozit skor) birim testleri."""
from crypto_signals.providers import Candles
from crypto_signals.signals import STRONG, WEAK, analyze, rating_for


def _make_candles(closes, highs=None, lows=None, volumes=None):
    n = len(closes)
    return Candles(
        opens=list(closes),
        highs=highs or [c * 1.01 for c in closes],
        lows=lows or [c * 0.99 for c in closes],
        closes=list(closes),
        volumes=volumes or [1000.0] * n,
    )


def test_rating_thresholds():
    assert rating_for(0.5) == STRONG
    assert rating_for(-0.5) == WEAK
    assert rating_for(0.0) == "🟡 NÖTR"


def _trending(start, step, n=220):
    # Genel trend + düzenli küçük geri çekilme (gerçekçi; RSI uçlara yapışmaz)
    out = []
    price = start
    for i in range(n):
        price += step
        if i % 4 == 0:
            price -= step * 1.6  # geri çekilme
        out.append(price)
    return out


def test_strong_uptrend_is_bullish():
    # Sağlıklı yükseliş + artan hacim → güçlü boğa konfluensi
    closes = _trending(100.0, 1.0)
    volumes = [1000.0 + i * 8 for i in range(len(closes))]
    candles = _make_candles(closes, volumes=volumes)
    a = analyze("UP", candles, change_pct_24h=8.0, fear_greed=15)
    assert a.composite > 0.35
    assert a.rating == STRONG
    assert a.bull_prob > 60


def test_strong_downtrend_is_bearish():
    closes = _trending(320.0, -1.0)
    volumes = [3000.0 - i * 8 for i in range(len(closes))]
    candles = _make_candles(closes, volumes=volumes)
    a = analyze("DOWN", candles, change_pct_24h=-8.0, fear_greed=85)
    assert a.composite < -0.35
    assert a.rating == WEAK
    assert a.bull_prob < 40


def test_composite_in_range():
    closes = [100.0 + (i % 5) for i in range(220)]
    candles = _make_candles(closes)
    a = analyze("FLAT", candles, change_pct_24h=0.0, fear_greed=50)
    assert -1.0 <= a.composite <= 1.0
    assert 0.0 <= a.bull_prob <= 100.0
    assert len(a.verdicts) == 8


def test_stop_below_price_when_atr_available():
    closes = [100.0 + i for i in range(220)]
    candles = _make_candles(closes)
    a = analyze("UP", candles, change_pct_24h=1.0, fear_greed=50)
    assert a.stop_suggestion is not None
    assert a.stop_suggestion < a.price
