"""Periyodik tarama + sinyal yaşam döngüsü — ayrı thread.

Histerezis: bir coin `ALERT_SCORE_THRESHOLD`'u aşınca "YENİ SİNYAL" açılır;
skor `SIGNAL_EXIT_THRESHOLD`'un altına düşene ya da yapısal kırılma olana kadar
"açık" kalır, sonra "FORMASYON BOZULDU" raporu gönderilir.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, List, Optional

from . import formatting
from .config import Config
from .providers import BinanceProvider, FearGreedProvider
from .signals import WEAK, Context, analyze, market_regime_score

log = logging.getLogger(__name__)

# Telegram mesajı gönderen callback: (chat_id, markdown_text, remove_symbol) -> None
# remove_symbol verilirse mesajın altına "radardan çıkar" butonu eklenir.
Notifier = Callable[[int, str, Optional[str]], None]


class Scheduler:
    def __init__(
        self,
        config: Config,
        storage,
        binance: BinanceProvider,
        fng: FearGreedProvider,
        notify: Notifier,
    ):
        self.cfg = config
        self.storage = storage
        self.binance = binance
        self.fng = fng
        self.notify = notify
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="scanner", daemon=True)
        self._thread.start()
        log.info("Scheduler başladı (her %s dk).", self.cfg.scan_interval_min)

    def stop(self) -> None:  # pragma: no cover
        self._stop.set()

    def _run(self) -> None:
        # İlk taramayı 15 sn sonra yap (bot ayağa kalksın), sonra periyodik.
        first = True
        while not self._stop.is_set():
            delay = 15 if first else self.cfg.scan_interval_min * 60
            if self._stop.wait(delay):
                break
            first = False
            try:
                self.scan_once()
            except Exception:  # tek tarama hatası thread'i öldürmesin
                log.exception("Tarama sırasında hata")

    def universe(self) -> List[str]:
        """Taranacak evren: yalnızca takip edilen coinler.

        = tüm /liste (watchlist) + tüm /radar + açık sinyaller. Artık dinamik
        top-N taranmıyor; geniş tarama yalnızca /check ile anlık yapılır. Açık
        sinyaller her zaman dahildir ki formasyon/stop uyarısı garanti gönderilsin.
        """
        symbols = set(self.storage.all_watched_symbols())
        symbols |= set(self.storage.all_radar_symbols())
        symbols |= {s.symbol for s in self.storage.list_open_signals()}
        return sorted(symbols)

    def _recipients(self, symbol: str) -> List[int]:
        """Bu sembolü takip eden (liste VEYA radar) chat_id'ler."""
        return list(
            set(self.storage.subscribers_watching(symbol))
            | set(self.storage.subscribers_radar(symbol))
        )

    def _btc_regime(self) -> Optional[float]:
        try:
            btc = self.binance.fetch_candles("BTC", limit=250)
            return market_regime_score(btc.closes)
        except Exception:
            log.warning("BTC rejimi hesaplanamadı", exc_info=True)
            return None

    def scan_once(self) -> None:
        symbols = self.universe()
        if not symbols:
            return
        fng_value = self.fng.fetch()
        btc_regime = self._btc_regime()  # tüm tarama için bir kez
        log.info("Tarama: %s sembol (BTC rejim %s)", len(symbols), btc_regime)
        for symbol in symbols:
            try:
                self._scan_symbol(symbol, fng_value, btc_regime)
            except Exception:
                log.warning("Sembol taranamadı: %s", symbol, exc_info=True)
            time.sleep(0.15)  # hafif throttle — oran limiti

    def _scan_symbol(
        self, symbol: str, fng_value: Optional[int], btc_regime: Optional[float]
    ) -> None:
        candles = self.binance.fetch_candles(symbol, limit=250)
        if len(candles) < 60:
            return
        ticker = self.binance.ticker_for(symbol)
        change_24h = ticker.change_pct if ticker else None
        quote_volume = ticker.quote_volume if ticker else None
        premium = (
            self.binance.fetch_premium(symbol)
            if self.cfg.enable_futures_basis
            else None
        )
        try:
            weekly = self.binance.fetch_candles(symbol, interval="1w", limit=60).closes
        except Exception:
            weekly = None
        ctx = Context(
            change_pct_24h=change_24h,
            fear_greed=fng_value,
            premium=premium,
            weekly_closes=weekly,
            btc_regime=None if symbol == "BTC" else btc_regime,
            quote_volume=quote_volume,
            min_quote_volume=self.cfg.min_quote_volume,
        )
        analysis = analyze(
            symbol, candles, ctx, strong_threshold=self.cfg.alert_score_threshold
        )
        self.storage.upsert_snapshot(
            symbol, analysis.composite, analysis.rating, analysis.price
        )
        self._lifecycle(symbol, analysis)

    def _lifecycle(self, symbol: str, analysis) -> None:
        open_signal = self.storage.get_open_signal(symbol)
        if open_signal is None:
            # Takip edilen coin güçlendi → yeni sinyal aç (giriş + 2×ATR stop)
            if analysis.composite >= self.cfg.alert_score_threshold:
                self.storage.upsert_open_signal(
                    symbol,
                    analysis.price,
                    analysis.rating,
                    analysis.composite,
                    stop=analysis.stop_suggestion,
                )
                self._broadcast(symbol, formatting.format_new_signal(analysis))
            return

        # 1) Fiyat-bazlı 2×ATR stop kırılması (skor ne olursa olsun)
        if open_signal.stop is not None and analysis.price <= open_signal.stop:
            msg = formatting.format_stop_hit(open_signal, analysis.price)
            self.storage.delete_open_signal(symbol)
            self._broadcast(symbol, msg, offer_remove=True)
            return

        # 2) Skor-bazlı formasyon bozulması
        structural_break = analysis.rating == WEAK
        below_exit = analysis.composite < self.cfg.signal_exit_threshold
        if structural_break or below_exit:
            reason = (
                "yapısal kırılma (rating ZAYIF)"
                if structural_break
                else f"skor çıkış eşiğinin altında (%{analysis.bull_prob:.0f})"
            )
            msg = formatting.format_signal_exit(open_signal, analysis.price, reason)
            self.storage.delete_open_signal(symbol)
            self._broadcast(symbol, msg, offer_remove=True)
        else:
            # Hâlâ açık — skoru güncelle (giriş fiyatı ve stop korunur)
            self.storage.upsert_open_signal(
                symbol,
                open_signal.entry_price,
                analysis.rating,
                analysis.composite,
                stop=open_signal.stop,
            )

    def _broadcast(self, symbol: str, text: str, offer_remove: bool = False) -> None:
        # Radar uyarılarında "radardan çıkar" butonu yalnızca o coini radarında
        # tutan kullanıcıya gösterilir.
        radar_subs = (
            set(self.storage.subscribers_radar(symbol)) if offer_remove else set()
        )
        for chat_id in self._recipients(symbol):
            remove_symbol = symbol if chat_id in radar_subs else None
            try:
                self.notify(chat_id, text, remove_symbol)
            except Exception:
                log.warning("Bildirim gönderilemedi: %s", chat_id, exc_info=True)
