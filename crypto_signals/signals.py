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
    available: bool = True  # veri yoksa False → kompozit ortalamaya katılmaz


def _na(name: str, weight: float, detail: str = "veri yok") -> "SignalVerdict":
    """Verisi olmayan sinyal — nötr değil, ortalamadan tamamen çıkarılır."""
    return SignalVerdict(name, 0.0, weight, detail, available=False)


@dataclass
class Context:
    """analyze()'a verilen piyasa bağlamı (hepsi opsiyonel)."""

    change_pct_24h: Optional[float] = None
    fear_greed: Optional[int] = None
    premium: Optional[Premium] = None
    weekly_closes: Optional[List[float]] = None
    btc_regime: Optional[float] = None  # -1..+1 ; None => BTC'nin kendisi / veri yok
    quote_volume: Optional[float] = None
    min_quote_volume: float = 0.0


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
    regime: str = "RANGE"
    notes: List[str] = field(default_factory=list)


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def detect_regime(candles: Candles):
    """Piyasa rejimi: TREND_UP / TREND_DOWN / RANGE (ADX + SMA hizası)."""
    closes = candles.closes
    adx_v = ind.adx(candles.highs, candles.lows, closes, 14)
    s50 = ind.sma(closes, 50)
    s200 = ind.sma(closes, 200)
    if adx_v is not None and adx_v >= 25 and s50 is not None and s200 is not None:
        if closes[-1] > s50 and s50 >= s200:
            return "TREND_UP", adx_v
        if closes[-1] < s50 and s50 <= s200:
            return "TREND_DOWN", adx_v
    return "RANGE", adx_v


def market_regime_score(btc_closes: List[float]) -> Optional[float]:
    """BTC günlük kapanışlarından piyasa rejim skoru [-1,+1]."""
    if len(btc_closes) < 60:
        return None
    price = btc_closes[-1]
    s50 = ind.sma(btc_closes, 50)
    s200 = ind.sma(btc_closes, 200)
    score = 0.0
    if s50 is not None:
        score += 0.4 if price > s50 else -0.4
    if s50 is not None and s200 is not None:
        score += 0.3 if s50 > s200 else -0.3
    if len(btc_closes) >= 15:
        chg = (price - btc_closes[-15]) / btc_closes[-15]
        score += _clamp(chg / 0.1) * 0.3
    return _clamp(score)


# --- Tekil sinyaller ---------------------------------------------------------


def trend_signal(closes: List[float]) -> SignalVerdict:
    price = closes[-1]
    s50 = ind.sma(closes, 50)
    s200 = ind.sma(closes, 200)
    if s50 is None or s200 is None:
        return _na("Trend (SMA50/200)", 1.5, "yetersiz veri")
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
        return _na("Golden/Death Cross", 1.2, "yetersiz veri")
    prev_diff = s50[-2] - s200[-2]
    curr_diff = s50[-1] - s200[-1]
    if prev_diff <= 0 < curr_diff:
        return SignalVerdict("Golden/Death Cross", 1.0, 1.2, "Golden Cross (yeni)")
    if prev_diff >= 0 > curr_diff:
        return SignalVerdict("Golden/Death Cross", -1.0, 1.2, "Death Cross (yeni)")
    score = 0.3 if curr_diff > 0 else -0.3
    return SignalVerdict("Golden/Death Cross", score, 1.2, "kesişim yok")


def rsi_signal(closes: List[float], regime: str = "RANGE") -> SignalVerdict:
    value = ind.rsi(closes, 14)
    if value is None:
        return _na("RSI", 1.0, "yetersiz veri")
    if regime == "TREND_UP":
        # Trendde yüksek RSI = momentum onayı (ayı değil). Sadece blow-off'ta fade.
        score = _clamp((value - 45.0) / 30.0)
        if value > 82:
            score = _clamp(score - (value - 82.0) / 15.0)
        detail = f"RSI={value:.1f} (trend modu)"
    elif regime == "TREND_DOWN":
        # Düşüş trendinde yüksek RSI = satış fırsatı (ayı)
        score = _clamp((45.0 - value) / 30.0)
        detail = f"RSI={value:.1f} (düşüş modu)"
    else:
        # Yatay: klasik mean-reversion (<30 boğa, >70 ayı)
        score = _clamp((50.0 - value) / 20.0)
        detail = f"RSI={value:.1f} (yatay)"
    return SignalVerdict("RSI", score, 1.0, detail)


def macd_signal(closes: List[float]) -> SignalVerdict:
    _, _, hist = ind.macd(closes)
    h_now = hist[-1]
    h_prev = hist[-2] if len(hist) >= 2 else None
    if h_now is None:
        return _na("MACD", 1.2, "yetersiz veri")
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
        return _na("Hacim trendi", 0.8, "yetersiz veri")
    ratio = short / long
    score = _clamp((ratio - 1.0) * 2.0)
    return SignalVerdict("Hacim trendi", score, 0.8, f"7g/30g hacim={ratio:.2f}")


def breakout_signal(candles: Candles, lookback: int = 30) -> SignalVerdict:
    highs = candles.highs
    lows = candles.lows
    vols = candles.volumes
    price = candles.closes[-1]
    if len(highs) < lookback + 1:
        return _na("30g Kırılım", 1.2, "yetersiz veri")
    window_high = max(highs[-lookback - 1 : -1])
    window_low = min(lows[-lookback - 1 : -1])
    vol_avg = ind.sma(vols, lookback) or 0.0
    vol_confirm = vol_avg > 0 and vols[-1] >= 1.5 * vol_avg
    if price >= window_high:
        if vol_confirm:
            return SignalVerdict("30g Kırılım", 1.0, 1.2, "30g direnci kırdı (hacim teyitli)")
        # Hacimsiz kırılım = fakeout riski → çok daha zayıf puan
        return SignalVerdict(
            "30g Kırılım", 0.35, 1.2, "30g direnci kırdı (HACİM ZAYIF — fakeout riski)"
        )
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
        return _na("24s Momentum", 1.0)
    score = _clamp(change_pct_24h / 5.0)
    return SignalVerdict("24s Momentum", score, 1.0, f"24s={change_pct_24h:+.2f}%")


def fear_greed_signal(value: Optional[int]) -> SignalVerdict:
    if value is None:
        return _na("Fear & Greed", 0.6)
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
        return _na(name, 1.0)
    basis = premium.basis_pct
    score = _clamp(basis / 0.5)
    funding_pct = premium.funding * 100.0
    detail = f"baz {basis:+.3f}% · funding {funding_pct:+.4f}%"
    return SignalVerdict(name, score, 1.0, detail)


def overextension_signal(
    candles: Candles, change_pct_24h: Optional[float]
) -> SignalVerdict:
    """Tepeden alma / kovalama cezası. Sadece aşırı uzamada negatif puanlar."""
    closes = candles.closes
    price = closes[-1]
    s20 = ind.sma(closes, 20)
    atr = ind.atr(candles.highs, candles.lows, closes, 14)
    if s20 is None or atr is None or atr == 0:
        return _na("Aşırı Uzama", 1.0, "yetersiz veri")
    ext = (price - s20) / atr
    score = 0.0
    reasons = []
    if ext > 2.5:
        score -= _clamp((ext - 2.5) / 2.0)
        reasons.append(f"{ext:.1f}×ATR uzakta")
    if change_pct_24h is not None and change_pct_24h > 25:
        score -= 0.5
        reasons.append(f"24s +%{change_pct_24h:.0f} (kovalama)")
    if score == 0.0:
        # Aşırı uzama yok → nötr oy olarak skoru bozma, ortalamadan çıkar
        return _na("Aşırı Uzama", 1.0, f"normal ({ext:+.1f}×ATR)")
    return SignalVerdict("Aşırı Uzama", _clamp(score), 1.0, "; ".join(reasons))


def btc_regime_signal(btc_regime: Optional[float]) -> SignalVerdict:
    if btc_regime is None:
        return _na("Piyasa (BTC) Rejimi", 1.5)
    return SignalVerdict(
        "Piyasa (BTC) Rejimi", _clamp(btc_regime), 1.5, f"BTC rejim {btc_regime:+.2f}"
    )


def weekly_trend_signal(weekly_closes: Optional[List[float]]) -> SignalVerdict:
    if not weekly_closes or len(weekly_closes) < 10:
        return _na("Haftalık Trend", 1.3, "yetersiz veri")
    price = weekly_closes[-1]
    w_sma = ind.sma(weekly_closes, 20) or ind.sma(weekly_closes, 10)
    score = 0.0
    if w_sma is not None:
        score += 0.6 if price > w_sma else -0.6
    if len(weekly_closes) >= 5:
        chg = (price - weekly_closes[-5]) / weekly_closes[-5]
        score += _clamp(chg / 0.2) * 0.4
    score = _clamp(score)
    return SignalVerdict(
        "Haftalık Trend", score, 1.3, "haftalık " + ("yukarı" if score >= 0 else "aşağı")
    )


# --- Motor -------------------------------------------------------------------

# Rejime göre ağırlık çarpanları: trendde trend/kırılım baskın, yatayda RSI baskın.
REGIME_MULTIPLIERS = {
    "TREND_UP": {
        "Trend (SMA50/200)": 1.3, "Golden/Death Cross": 1.3, "30g Kırılım": 1.3,
        "MACD": 1.2, "Haftalık Trend": 1.2, "RSI": 0.6,
    },
    "TREND_DOWN": {
        "Trend (SMA50/200)": 1.3, "Golden/Death Cross": 1.3, "30g Kırılım": 1.3,
        "MACD": 1.2, "Haftalık Trend": 1.2, "RSI": 0.6,
    },
    "RANGE": {
        "Trend (SMA50/200)": 0.7, "Golden/Death Cross": 0.7, "30g Kırılım": 0.8,
        "RSI": 1.4,
    },
}


def rating_for(composite: float, strong_threshold: float = 0.35) -> str:
    if composite >= strong_threshold:
        return STRONG
    if composite <= -strong_threshold:
        return WEAK
    return NEUTRAL


def analyze(
    symbol: str,
    candles: Candles,
    ctx: Optional[Context] = None,
    strong_threshold: float = 0.40,
) -> Analysis:
    ctx = ctx or Context()
    closes = candles.closes
    price = closes[-1] if closes else 0.0
    regime, _adx = detect_regime(candles)

    verdicts = [
        trend_signal(closes),
        cross_signal(closes),
        rsi_signal(closes, regime),
        macd_signal(closes),
        volume_signal(candles),
        breakout_signal(candles),
        momentum_signal(ctx.change_pct_24h),
        fear_greed_signal(ctx.fear_greed),
        basis_signal(ctx.premium),
        weekly_trend_signal(ctx.weekly_closes),
        btc_regime_signal(ctx.btc_regime),
        overextension_signal(candles, ctx.change_pct_24h),
    ]
    # Rejime göre ağırlıklandır; yalnızca verisi olan sinyaller ortalamaya girer.
    mult = REGIME_MULTIPLIERS.get(regime, {})
    active = [v for v in verdicts if v.available]
    total_weight = sum(v.weight * mult.get(v.name, 1.0) for v in active) or 1.0
    composite = (
        sum(v.score * v.weight * mult.get(v.name, 1.0) for v in active) / total_weight
    )
    composite = _clamp(composite)

    notes: List[str] = []
    # --- Veto / kapı katmanı (manipülasyon ve makro koruması) ---
    low_liq = (
        ctx.quote_volume is not None
        and ctx.min_quote_volume > 0
        and ctx.quote_volume < ctx.min_quote_volume
    )
    if low_liq and composite > 0:
        notes.append("⚠️ Düşük likidite — manipülasyona açık, sinyal zayıflatıldı")
        composite *= 0.3
    if ctx.btc_regime is not None and ctx.btc_regime <= -0.5 and composite > 0:
        notes.append(f"⚠️ BTC rejimi zayıf ({ctx.btc_regime:+.2f}) — alt long riskli")
        composite *= 0.5
    s200 = ind.sma(closes, 200)
    if s200 is not None and price < s200 and composite > 0:
        notes.append("⚠️ Fiyat SMA200 altında (makro ayı) — GÜÇLÜ verilmez")
        composite = min(composite, strong_threshold - 0.05)
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
        regime=regime,
        notes=notes,
    )


# --- Anti-FOMO değerlendirme (rasyonel çapa) --------------------------------

AVOID = "AVOID"
WAIT = "WAIT"
CONSIDER = "CONSIDER"

_DISCIPLINE_NOTES = {
    AVOID: "Bu kurulumda sana edge yok. Para kaybetmemenin en iyi yolu işlem yapmamaktır.",
    WAIT: "Acele etme. Kötü giriş, iyi coini bile kaybettirir; geri çekilme/teyit bekle.",
    CONSIDER: "Koşullar uygun ama boyutu küçük tut, stopuna sadık kal, plan dışı ekleme yapma.",
}


@dataclass
class Check:
    status: str  # "ok" | "warn" | "bad"
    label: str


@dataclass
class Evaluation:
    verdict: str  # AVOID | WAIT | CONSIDER
    headline: str
    checks: List[Check] = field(default_factory=list)
    rr: Optional[float] = None
    target: Optional[float] = None
    note: str = ""


def evaluate(analysis: Analysis, candles: Candles, ctx: Context) -> Evaluation:
    """Dürtüsel alım öncesi soğuk ikinci görüş + net karar.

    Varsayılan eğilim ihtiyatlıdır: net avantaj yoksa 'BEKLE' der.
    """
    price = analysis.price
    closes = candles.closes
    checks: List[Check] = []
    flags = set()

    # Likidite
    if ctx.quote_volume is not None and ctx.min_quote_volume > 0:
        ok = ctx.quote_volume >= ctx.min_quote_volume
        checks.append(Check("ok" if ok else "bad", f"Likidite ${ctx.quote_volume / 1e6:.0f}M"))
        if not ok:
            flags.add("liq")

    # Aşırı uzama (tepeden alma)
    s20 = ind.sma(closes, 20)
    atr = ind.atr(candles.highs, candles.lows, closes, 14)
    if s20 is not None and atr:
        ext = (price - s20) / atr
        if ext > 2.5:
            checks.append(Check("bad", f"Aşırı uzama {ext:.1f}×ATR (tepeye yakın)"))
            flags.add("ext")
        else:
            checks.append(Check("ok", f"Uzama normal ({ext:+.1f}×ATR)"))

    # 24s pump (kovalama)
    ch = ctx.change_pct_24h
    if ch is not None:
        if ch > 25:
            checks.append(Check("bad", f"24s +%{ch:.0f} (pompalanmış, kovalama)"))
            flags.add("pump")
        elif ch < -15:
            checks.append(Check("warn", f"24s %{ch:.0f} (sert düşüş)"))
        else:
            checks.append(Check("ok", f"24s %{ch:+.0f} (normal)"))

    # BTC rejimi
    if ctx.btc_regime is not None:
        if ctx.btc_regime <= -0.5:
            checks.append(Check("bad", f"BTC rejimi {ctx.btc_regime:+.2f} (risk-off)"))
            flags.add("btc")
        elif ctx.btc_regime < 0:
            checks.append(Check("warn", f"BTC rejimi {ctx.btc_regime:+.2f} (zayıf)"))
        else:
            checks.append(Check("ok", f"BTC rejimi {ctx.btc_regime:+.2f} (uygun)"))

    # Makro (SMA200)
    s200 = ind.sma(closes, 200)
    if s200 is not None:
        if price < s200:
            checks.append(Check("bad", "Fiyat SMA200 altında (makro ayı)"))
            flags.add("macro")
        else:
            checks.append(Check("ok", "SMA200 üstünde (makro yukarı)"))

    # Sinyal gücü
    if analysis.composite < 0 or analysis.rating == WEAK:
        checks.append(Check("bad", f"Sinyal zayıf (%{analysis.bull_prob:.0f})"))
        flags.add("weak")
    elif analysis.rating == STRONG:
        checks.append(Check("ok", f"Sinyal güçlü (%{analysis.bull_prob:.0f})"))
    else:
        checks.append(Check("warn", f"Sinyal nötr (%{analysis.bull_prob:.0f})"))
        flags.add("neutral")

    # Risk/Ödül
    rr = None
    target = None
    if analysis.stop_suggestion is not None and analysis.stop_suggestion < price:
        risk = price - analysis.stop_suggestion
        highs = candles.highs
        if len(highs) >= 61:
            resistance = max(highs[-61:-1])
        elif len(highs) >= 2:
            resistance = max(highs[:-1])
        else:
            resistance = price
        target = resistance if resistance > price * 1.01 else price + 2.0 * risk
        reward = target - price
        rr = reward / risk if risk > 0 else None
        if rr is not None:
            if rr < 1.5:
                checks.append(Check("bad", f"R:R {rr:.1f} (ödül < risk)"))
                flags.add("rr")
            else:
                checks.append(Check("ok", f"R:R {rr:.1f}"))

    # --- Karar (ihtiyatlı öncelik sırası) ---
    if "liq" in flags:
        verdict, head = AVOID, "Düşük likidite — manipülasyon yemi, uzak dur"
    elif "weak" in flags:
        verdict, head = AVOID, "Sinyal zaten zayıf — almak için sebep yok"
    elif "pump" in flags or "ext" in flags:
        verdict, head = WAIT, "Pompalanmış/aşırı uzamış — kovalama, geri çekilmeyi bekle"
    elif "macro" in flags or "btc" in flags:
        verdict, head = WAIT, "Makro/BTC uygun değil — piyasa toparlayınca bak"
    elif "rr" in flags:
        verdict, head = WAIT, "Risk/ödül kötü — daha iyi bir giriş bekle"
    elif analysis.rating == STRONG and not flags:
        verdict, head = CONSIDER, "Koşullar uygun — kurallarına göre değerlendirilebilir"
    else:
        verdict, head = WAIT, "Net avantaj yok — acele etme, nakit de bir pozisyondur"

    return Evaluation(verdict, head, checks, rr, target, _DISCIPLINE_NOTES[verdict])
