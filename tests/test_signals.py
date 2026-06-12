"""Sinyal motoru (kompozit skor) birim testleri."""
from crypto_signals.providers import Candles, Premium
from crypto_signals.signals import STRONG, WEAK, analyze, basis_signal, rating_for


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
    assert len(a.verdicts) == 9


def test_basis_signal_positive_when_futures_above_spot():
    # Vadeli > spot → pozitif baz → boğa
    up = basis_signal(Premium(mark=101.0, index=100.0, funding=0.0001))
    assert up.score > 0
    # Vadeli < spot → negatif baz → ayı
    down = basis_signal(Premium(mark=99.0, index=100.0, funding=-0.0001))
    assert down.score < 0
    # Veri yoksa nötr, ama yine de bir oy (ağırlık) taşır
    none = basis_signal(None)
    assert none.score == 0.0 and none.weight > 0


def test_unavailable_signal_does_not_drag_score():
    # Vadeli verisi yoksa (engelli), skor; vadeli sinyali HİÇ yokmuş gibi olmalı —
    # nötr oy olarak 50%'ye doğru ezilmemeli.
    closes = _trending(100.0, 1.0)
    candles = _make_candles(closes, volumes=[1000.0 + i * 8 for i in range(len(closes))])
    with_blocked = analyze("X", candles, 8.0, 15, premium=None)
    # Aynı analizi elle, vadeli sinyali tamamen çıkararak doğrula
    blocked_verdict = [v for v in with_blocked.verdicts if v.name == "Vadeli/Spot Farkı"][0]
    assert blocked_verdict.available is False
    # Engelli vadeli, kompozit GÜÇLÜ kalmasını engellememeli
    assert with_blocked.composite > 0.35
    assert with_blocked.rating == STRONG


def test_basis_signal_in_analysis_shifts_score():
    closes = [100.0 + (i % 5) for i in range(220)]
    candles = _make_candles(closes)
    bullish_basis = Premium(mark=100.6, index=100.0, funding=0.0003)
    a = analyze("FOO", candles, 0.0, 50, premium=bullish_basis)
    base = analyze("FOO", candles, 0.0, 50, premium=None)
    assert a.composite > base.composite  # pozitif baz skoru yukarı çeker


def test_stop_below_price_when_atr_available():
    closes = [100.0 + i for i in range(220)]
    candles = _make_candles(closes)
    a = analyze("UP", candles, change_pct_24h=1.0, fear_greed=50)
    assert a.stop_suggestion is not None
    assert a.stop_suggestion < a.price
