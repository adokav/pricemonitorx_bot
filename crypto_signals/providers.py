"""Veri sağlayıcı adapter'ları — anahtarsız public API'ler.

* `BinanceProvider`  : OHLCV (günlük mum) + 24s ticker (toplu).
* `FearGreedProvider`: alternative.me Korku & Açgözlülük endeksi.

Yeni borsa eklemek = yeni adapter; çekirdek (signals/scheduler) değişmez.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests

log = logging.getLogger(__name__)

# Binance bazı bulut/ABD IP'lerinden api.binance.com'a 451/403 döndürür
# (Render Oregon = ABD). Bu yüzden coğrafi engele takılmayan public market-data
# mirror'larını sırayla deneriz. İlk başarılı host yanıtı kullanılır.
BINANCE_HOSTS = [
    "https://data-api.binance.vision",  # yalnızca public market data — genelde engelsiz
    "https://api-gcp.binance.com",
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
]
BINANCE_BASE = BINANCE_HOSTS[0]
# Vadeli (USDⓈ-M futures) — premiumIndex tek çağrıda markPrice+indexPrice+funding verir.
# Bu host bazı ABD bulut IP'lerinden engellenebilir; erişilemezse devre kesici devreye girer.
BINANCE_FUTURES_BASE = "https://fapi.binance.com"
FNG_URL = "https://api.alternative.me/fng/"

# Dinamik evrenden elenecek stable/fiat tabanlar.
DEFAULT_EXCLUDE_BASES = {
    "USDT", "USDC", "BUSD", "TUSD", "DAI", "FDUSD", "USDP", "GUSD",
    "EUR", "GBP", "TRY", "AUD", "BRL", "RUB", "UAH", "NGN", "ZAR",
    "PAX", "SUSD", "USTC", "EURI", "AEUR", "WBTC", "WBETH",
}


@dataclass
class Candles:
    """Günlük OHLCV serisi (eski→yeni)."""

    opens: List[float] = field(default_factory=list)
    highs: List[float] = field(default_factory=list)
    lows: List[float] = field(default_factory=list)
    closes: List[float] = field(default_factory=list)
    volumes: List[float] = field(default_factory=list)

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self.closes)


@dataclass
class Ticker24h:
    symbol: str
    last_price: float
    change_pct: float
    quote_volume: float


@dataclass
class Premium:
    """Vadeli (futures) prim bilgisi — markPrice vs indexPrice + funding."""

    mark: float  # vadeli işaret fiyatı
    index: float  # spot endeks fiyatı
    funding: float  # son funding oranı (oran, yüzde değil)

    @property
    def basis_pct(self) -> float:
        if self.index <= 0:
            return 0.0
        return (self.mark - self.index) / self.index * 100.0


def _request_json(
    url: str,
    params: Optional[dict] = None,
    *,
    retries: int = 3,
    timeout: float = 10.0,
):
    last_exc: Optional[Exception] = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # ağ/oran limiti — üstel geri çekilme
            last_exc = exc
            wait = 2 ** attempt
            log.warning("İstek başarısız (%s), %ss sonra tekrar: %s", url, wait, exc)
            time.sleep(wait)
    raise RuntimeError(f"İstek {retries} denemede başarısız: {url}") from last_exc


class BinanceProvider:
    def __init__(
        self,
        quote_asset: str = "USDT",
        hosts: Optional[List[str]] = None,
    ):
        self.quote_asset = quote_asset.upper()
        self.hosts = [h.rstrip("/") for h in (hosts or BINANCE_HOSTS)]
        self._host_idx = 0  # son çalışan host'u hatırla
        self._ticker_cache: Optional[List[Ticker24h]] = None
        self._ticker_cache_ts: float = 0.0
        # Vadeli prim devre kesici: art arda başarısızlıkta geçici devre dışı bırak
        self._fut_fail_streak = 0
        self._fut_cooldown_until = 0.0

    def _get(self, path: str, params: Optional[dict] = None):
        """Çalışan ilk host'tan JSON döner; coğrafi engelde sıradakine geçer."""
        n = len(self.hosts)
        last_exc: Optional[Exception] = None
        for offset in range(n):
            idx = (self._host_idx + offset) % n
            url = f"{self.hosts[idx]}{path}"
            try:
                data = _request_json(url, params=params, retries=2)
                self._host_idx = idx  # bu host'u tercih et
                return data
            except Exception as exc:
                last_exc = exc
                log.warning("Binance host başarısız (%s), sıradaki deneniyor", self.hosts[idx])
        raise RuntimeError("Tüm Binance host'ları başarısız") from last_exc

    def symbol_pair(self, base: str) -> str:
        base = base.upper()
        if base.endswith(self.quote_asset):
            return base
        return f"{base}{self.quote_asset}"

    def fetch_candles(self, base: str, interval: str = "1d", limit: int = 250) -> Candles:
        pair = self.symbol_pair(base)
        data = self._get(
            "/api/v3/klines",
            params={"symbol": pair, "interval": interval, "limit": limit},
        )
        candles = Candles()
        for row in data:
            # [openTime, open, high, low, close, volume, ...]
            candles.opens.append(float(row[1]))
            candles.highs.append(float(row[2]))
            candles.lows.append(float(row[3]))
            candles.closes.append(float(row[4]))
            candles.volumes.append(float(row[5]))
        return candles

    def fetch_all_tickers(self, *, cache_seconds: float = 60.0) -> List[Ticker24h]:
        now = time.time()
        if self._ticker_cache is not None and now - self._ticker_cache_ts < cache_seconds:
            return self._ticker_cache
        data = self._get("/api/v3/ticker/24hr")
        tickers: List[Ticker24h] = []
        for row in data:
            try:
                tickers.append(
                    Ticker24h(
                        symbol=row["symbol"],
                        last_price=float(row["lastPrice"]),
                        change_pct=float(row["priceChangePercent"]),
                        quote_volume=float(row["quoteVolume"]),
                    )
                )
            except (KeyError, ValueError):
                continue
        self._ticker_cache = tickers
        self._ticker_cache_ts = now
        return tickers

    def top_symbols(
        self, top_n: int, exclude_bases: Optional[set] = None
    ) -> List[str]:
        """Hacme göre ilk N tabanı (quote asset çıkarılmış) döndürür."""
        excludes = set(DEFAULT_EXCLUDE_BASES)
        if exclude_bases:
            excludes |= {b.upper() for b in exclude_bases}
        tickers = self.fetch_all_tickers()
        candidates = []
        for t in tickers:
            if not t.symbol.endswith(self.quote_asset):
                continue
            base = t.symbol[: -len(self.quote_asset)]
            if not base or base in excludes:
                continue
            candidates.append((base, t.quote_volume))
        candidates.sort(key=lambda x: x[1], reverse=True)
        return [base for base, _ in candidates[:top_n]]

    def ticker_for(self, base: str) -> Optional[Ticker24h]:
        pair = self.symbol_pair(base)
        for t in self.fetch_all_tickers():
            if t.symbol == pair:
                return t
        return None

    def fetch_premium(
        self,
        base: str,
        *,
        fail_threshold: int = 8,
        cooldown_seconds: float = 1800.0,
    ) -> Optional[Premium]:
        """Vadeli prim (markPrice vs indexPrice + funding) döner.

        Coğrafi engel ya da vadeli işlemi olmayan coin durumunda `None` döner —
        skorlamayı bozmaz. Art arda çok fazla başarısızlık olursa (host engelli
        gibi) devre kesici devreye girip belirli süre denemez, böylece her
        taramada boşa istek atılmaz.
        """
        if time.monotonic() < self._fut_cooldown_until:
            return None
        pair = self.symbol_pair(base)
        try:
            data = _request_json(
                f"{BINANCE_FUTURES_BASE}/fapi/v1/premiumIndex",
                params={"symbol": pair},
                retries=1,
                timeout=8.0,
            )
            index = float(data["indexPrice"])
            if index <= 0:
                return None
            self._fut_fail_streak = 0  # başarı → sayacı sıfırla
            return Premium(
                mark=float(data["markPrice"]),
                index=index,
                funding=float(data.get("lastFundingRate") or 0.0),
            )
        except Exception:
            self._fut_fail_streak += 1
            if self._fut_fail_streak >= fail_threshold:
                self._fut_cooldown_until = time.monotonic() + cooldown_seconds
                self._fut_fail_streak = 0
                log.warning(
                    "Vadeli (futures) veri alınamıyor; bu sinyal %.0f dk devre dışı.",
                    cooldown_seconds / 60.0,
                )
            return None


class FearGreedProvider:
    def __init__(self, url: str = FNG_URL):
        self.url = url
        self._cache: Optional[int] = None
        self._cache_ts: float = 0.0

    def fetch(self, *, cache_seconds: float = 600.0) -> Optional[int]:
        now = time.time()
        if self._cache is not None and now - self._cache_ts < cache_seconds:
            return self._cache
        try:
            data = _request_json(self.url, params={"limit": 1})
            value = int(data["data"][0]["value"])
        except Exception as exc:  # endeks opsiyonel — None ile devam
            log.warning("Fear & Greed alınamadı: %s", exc)
            return None
        self._cache = value
        self._cache_ts = now
        return value
