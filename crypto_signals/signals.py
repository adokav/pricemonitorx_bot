"""Sinyal motoru — 9 sinyalin konfluensi → tek kompozit skor.

Her sinyal `[-1, +1]` puan + ağırlık üretir; ağırlıklı ortalama kompozit skoru
verir. Tek sinyal değil, **sinyallerin hemfikir olması** belirleyicidir.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from . import indicators as ind
from .providers import Candles, Premium

# Rating etiketleri
STRONG = "🟢 GÜÇLÜ"
NEUTRAL = "🟡 NÖTR"
WEAK = "🔴 ZAYIF"


@dataclass
class SignalVerdict:
    name: str
    score: float  # [-1, +1]
    weight: float
    detail: str


@dataclass
class Analysis:
    symbol: str
    price: float
    composite: float  # [-1, +1]
    rating: str
    bull_prob: float  # %
    verdicts: List[SignalVerdict] = field(default_factory=list)
    atr: Optional[float] = None
    stop_suggestion: Optional[float] = None


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# --- Tekil sinyaller ---------------------------------------------------------


def trend_signal(closes: List[float]) -> SignalVerdict:
    price = closes[-1]
    s50 = ind.sma(closes, 50)
    s200 = ind.sma(closes, 200)
    if s50 is None or s200 is None:
        return SignalVerdict("Trend (SMA50/200)", 0.0, 1.5, "yetersiz veri")
    score = 0.0
    if price > s50:
        score += 0.5
    else:
        score -= 0.5
    if s50 > s200:
        score += 0.5
    else:
        score -= 0.5
    detail = f"fiyat {'>' if price > s50 else '<'} SMA50, SMA50 {'>' if s50 > s200 else '<'} SMA200"
    return SignalVerdict("Trend (SMA50/200)", _clamp(score), 1.5, detail)


def cross_signal(closes: List[float]) -> SignalVerdict:
    s50 = ind.sma_series(closes, 50)
    s200 = ind.sma_series(closes, 200)
    if s50[-1] is None or s200[-1] is None or s50[-2] is None or s200[-2] is None:
        return SignalVerdict("Golden/Death Cross", 0.0, 1.2, "yetersiz veri")
    prev_diff = s50[-2] - s200[-2]
    curr_diff = s50[-1] - s200[-1]
    if prev_diff <= 0 < curr_diff:
        return SignalVerdict("Golden/Death Cross", 1.0, 1.2, "Golden Cross (yeni)")
    if prev_diff >= 0 > curr_diff:
        return SignalVerdict("Golden/Death Cross", -1.0, 1.2, "Death Cross (yeni)")
    score = 0.3 if curr_diff > 0 else -0.3
    return SignalVerdict("Golden/Death Cross", score, 1.2, "kesişim yok")


def rsi_signal(closes: List[float]) -> SignalVerdict:
    value = ind.rsi(closes, 14)
    if value is None:
        return SignalVerdict("RSI", 0.0, 1.0, "yetersiz veri")
    # 50 nötr; <30 aşırı satım (boğa lehine), >70 aşırı alım (ayı lehine)
    score = _clamp((50.0 - value) / 20.0)
    return SignalVerdict("RSI", score, 1.0, f"RSI={value:.1f}")


def macd_signal(closes: List[float]) -> SignalVerdict:
    _, _, hist = ind.macd(closes)
    h_now = hist[-1]
    h_prev = hist[-2] if len(hist) >= 2 else None
    if h_now is None:
        return SignalVerdict("MACD", 0.0, 1.2, "yetersiz veri")
    score = 0.5 if h_now > 0 else -0.5
    if h_prev is not None:
        if h_now > h_prev:
            score += 0.5
        else:
            score -= 0.5
    return SignalVerdict("MACD", _clamp(score), 1.2, f"hist={h_now:.4f}")


def volume_signal(candles: Candles) -> SignalVerdict:
    vols = candles.volumes
    short = ind.sma(vols, 7)
    long = ind.sma(vols, 30)
    if short is None or long is None or long == 0:
        return SignalVerdict("Hacim trendi", 0.0, 0.8, "yetersiz veri")
    ratio = short / long
    score = _clamp((ratio - 1.0) * 2.0)
    return SignalVerdict("Hacim trendi", score, 0.8, f"7g/30g hacim={ratio:.2f}")


def breakout_signal(candles: Candles, lookback: int = 30) -> SignalVerdict:
    highs = candles.highs
    lows = candles.lows
    price = candles.closes[-1]
    if len(highs) < lookback + 1:
        return SignalVerdict("30g Kırılım", 0.0, 1.2, "yetersiz veri")
    window_high = max(highs[-lookback - 1 : -1])
    window_low = min(lows[-lookback - 1 : -1])
    if price >= window_high:
        return SignalVerdict("30g Kırılım", 1.0, 1.2, "30g direnci kırdı")
    if price <= window_low:
        return SignalVerdict("30g Kırılım", -1.0, 1.2, "30g desteği kırdı")
    span = window_high - window_low
    if span <= 0:
        return SignalVerdict("30g Kırılım", 0.0, 1.2, "düz aralık")
    # Aralık içinde konumu [-1,+1]'e ölçekle
    score = _clamp((price - window_low) / span * 2.0 - 1.0)
    return SignalVerdict("30g Kırılım", score, 1.2, "aralık içinde")


def momentum_signal(change_pct_24h: Optional[float]) -> SignalVerdict:
    if change_pct_24h is None:
        return SignalVerdict("24s Momentum", 0.0, 1.0, "veri yok")
    score = _clamp(change_pct_24h / 5.0)
    return SignalVerdict("24s Momentum", score, 1.0, f"24s={change_pct_24h:+.2f}%")


def fear_greed_signal(value: Optional[int]) -> SignalVerdict:
    if value is None:
        return SignalVerdict("Fear & Greed", 0.0, 0.6, "veri yok")
    # Kontraryan: aşırı korku (0) boğa lehine, aşırı açgözlülük (100) ayı lehine
    score = _clamp((50.0 - value) / 50.0)
    return SignalVerdict("Fear & Greed", score, 0.6, f"endeks={value}")


def basis_signal(premium: Optional[Premium]) -> SignalVerdict:
    """Vadeli (futures) fiyat ile spot fiyat farkı (baz/prim).

    Pozitif baz (vadeli > spot) = long baskısı/boğa eğilimi; negatif baz
    (vadeli < spot, backwardation) = short baskısı/ayı eğilimi. Tipik baz ±0.5%
    bandındadır, bu yüzden ±0.5% tam puana ölçeklenir. Funding oranı detayda.
    """
    name = "Vadeli/Spot Farkı"
    if premium is None or premium.index <= 0:
        return SignalVerdict(name, 0.0, 1.0, "veri yok")
    basis = premium.basis_pct
    score = _clamp(basis / 0.5)
    funding_pct = premium.funding * 100.0
    detail = f"baz {basis:+.3f}% · funding {funding_pct:+.4f}%"
    return SignalVerdict(name, score, 1.0, detail)


# --- Motor -------------------------------------------------------------------


def rating_for(composite: float, strong_threshold: float = 0.35) -> str:
    if composite >= strong_threshold:
        return STRONG
    if composite <= -strong_threshold:
        return WEAK
    return NEUTRAL


def analyze(
    symbol: str,
    candles: Candles,
    change_pct_24h: Optional[float],
    fear_greed: Optional[int],
    premium: Optional[Premium] = None,
    strong_threshold: float = 0.40,
) -> Analysis:
    closes = candles.closes
    price = closes[-1] if closes else 0.0
    verdicts = [
        trend_signal(closes),
        cross_signal(closes),
        rsi_signal(closes),
        macd_signal(closes),
        volume_signal(candles),
        breakout_signal(candles),
        momentum_signal(change_pct_24h),
        fear_greed_signal(fear_greed),
        basis_signal(premium),
    ]
    total_weight = sum(v.weight for v in verdicts) or 1.0
    composite = sum(v.score * v.weight for v in verdicts) / total_weight
    composite = _clamp(composite)
    bull_prob = (composite + 1.0) / 2.0 * 100.0

    atr_val = ind.atr(candles.highs, candles.lows, closes, 14)
    stop = price - 2.0 * atr_val if atr_val is not None else None

    return Analysis(
        symbol=symbol,
        price=price,
        composite=composite,
        rating=rating_for(composite, strong_threshold),
        bull_prob=bull_prob,
        verdicts=verdicts,
        atr=atr_val,
        stop_suggestion=stop,
    )
