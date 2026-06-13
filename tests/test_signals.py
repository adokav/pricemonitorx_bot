"""Sinyal motoru (kompozit skor) birim testleri."""
from crypto_signals.providers import Candles, Premium
from crypto_signals.signals import (
    AVOID,
    CONSIDER,
    STRONG,
    WAIT,
    WEAK,
    Context,
    analyze,
    basis_signal,
    evaluate,
    market_regime_score,
    rating_for,
)


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
    closes = _trending(100.0, 1.0)
    volumes = [1000.0 + i * 8 for i in range(len(closes))]
    candles = _make_candles(closes, volumes=volumes)
    a = analyze("UP", candles, Context(change_pct_24h=8.0, fear_greed=15))
    assert a.composite > 0.35
    assert a.rating == STRONG
    assert a.bull_prob > 60


def test_strong_downtrend_is_bearish():
    closes = _trending(320.0, -1.0)
    volumes = [3000.0 - i * 8 for i in range(len(closes))]
    candles = _make_candles(closes, volumes=volumes)
    a = analyze("DOWN", candles, Context(change_pct_24h=-8.0, fear_greed=85))
    assert a.composite < -0.35
    assert a.rating == WEAK
    assert a.bull_prob < 40


def test_composite_in_range():
    closes = [100.0 + (i % 5) for i in range(220)]
    candles = _make_candles(closes)
    a = analyze("FLAT", candles, Context(change_pct_24h=0.0, fear_greed=50))
    assert -1.0 <= a.composite <= 1.0
    assert 0.0 <= a.bull_prob <= 100.0
    assert len(a.verdicts) == 12  # 9 + haftalık + BTC rejim + aşırı uzama


def test_basis_signal_positive_when_futures_above_spot():
    up = basis_signal(Premium(mark=101.0, index=100.0, funding=0.0001))
    assert up.score > 0
    down = basis_signal(Premium(mark=99.0, index=100.0, funding=-0.0001))
    assert down.score < 0
    none = basis_signal(None)
    assert none.score == 0.0 and none.weight > 0


def test_unavailable_signal_does_not_drag_score():
    closes = _trending(100.0, 1.0)
    candles = _make_candles(closes, volumes=[1000.0 + i * 8 for i in range(len(closes))])
    a = analyze("X", candles, Context(change_pct_24h=8.0, fear_greed=15, premium=None))
    blocked = [v for v in a.verdicts if v.name == "Vadeli/Spot Farkı"][0]
    assert blocked.available is False
    assert a.composite > 0.35
    assert a.rating == STRONG


def test_basis_signal_in_analysis_shifts_score():
    closes = [100.0 + (i % 5) for i in range(220)]
    candles = _make_candles(closes)
    bull = analyze("FOO", candles, Context(fear_greed=50, premium=Premium(100.6, 100.0, 0.0003)))
    base = analyze("FOO", candles, Context(fear_greed=50, premium=None))
    assert bull.composite > base.composite


def test_stop_below_price_when_atr_available():
    closes = [100.0 + i for i in range(220)]
    candles = _make_candles(closes)
    a = analyze("UP", candles, Context(change_pct_24h=1.0, fear_greed=50))
    assert a.stop_suggestion is not None
    assert a.stop_suggestion < a.price


def test_btc_bear_regime_gates_alt_long():
    # Güçlü yükselen alt, ama BTC rejimi sert düşüşte → GÜÇLÜ verilmemeli
    closes = _trending(100.0, 1.0)
    candles = _make_candles(closes, volumes=[1000.0 + i * 8 for i in range(len(closes))])
    bull = analyze("ALT", candles, Context(change_pct_24h=8.0, fear_greed=15, btc_regime=0.5))
    gated = analyze("ALT", candles, Context(change_pct_24h=8.0, fear_greed=15, btc_regime=-0.8))
    assert gated.composite < bull.composite
    assert any("BTC" in n for n in gated.notes)


def test_low_liquidity_penalised():
    closes = _trending(100.0, 1.0)
    candles = _make_candles(closes, volumes=[1000.0 + i * 8 for i in range(len(closes))])
    liquid = analyze("ALT", candles, Context(change_pct_24h=8.0, fear_greed=15, quote_volume=1e9, min_quote_volume=3e7))
    illiquid = analyze("ALT", candles, Context(change_pct_24h=8.0, fear_greed=15, quote_volume=1e5, min_quote_volume=3e7))
    assert illiquid.composite < liquid.composite
    assert illiquid.rating != STRONG


def test_market_regime_score_direction():
    up = market_regime_score([100.0 + i for i in range(220)])
    down = market_regime_score([320.0 - i for i in range(220)])
    assert up is not None and up > 0
    assert down is not None and down < 0


def test_evaluate_pumped_coin_says_wait():
    closes = _trending(100.0, 1.0)
    candles = _make_candles(closes, volumes=[1000.0 + i * 8 for i in range(len(closes))])
    ctx = Context(change_pct_24h=40.0, fear_greed=80, btc_regime=0.4, quote_volume=1e9, min_quote_volume=3e7)
    a = analyze("PUMP", candles, ctx)
    ev = evaluate(a, candles, ctx)
    assert ev.verdict == WAIT
    assert any("pompalan" in c.label.lower() or "kovalama" in c.label.lower() for c in ev.checks)


def test_evaluate_low_liquidity_says_avoid():
    closes = _trending(100.0, 1.0)
    candles = _make_candles(closes, volumes=[1000.0 + i * 8 for i in range(len(closes))])
    ctx = Context(change_pct_24h=5.0, fear_greed=40, btc_regime=0.4, quote_volume=1e5, min_quote_volume=3e7)
    a = analyze("THIN", candles, ctx)
    ev = evaluate(a, candles, ctx)
    assert ev.verdict == AVOID


def test_evaluate_clean_strong_can_be_considered():
    closes = _trending(100.0, 1.0)
    candles = _make_candles(closes, volumes=[1000.0 + i * 8 for i in range(len(closes))])
    ctx = Context(change_pct_24h=4.0, fear_greed=40, btc_regime=0.5, quote_volume=1e9, min_quote_volume=3e7)
    a = analyze("CLEAN", candles, ctx)
    ev = evaluate(a, candles, ctx)
    assert ev.verdict in (CONSIDER, WAIT)  # uygun koşullarda en azından AVOID değil
    assert ev.verdict != AVOID
