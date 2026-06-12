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
from .signals import WEAK, analyze

log = logging.getLogger(__name__)

# Telegram mesajı gönderen callback: (chat_id, markdown_text) -> None
Notifier = Callable[[int, str], None]


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
        """Taranacak evren: açık sinyaller + watchlist'ler + dinamik top-N.

        Açık sinyaller her zaman dahil edilir; bir coin sinyal verdikten sonra
        hacmi düşüp top-N'den çıksa bile formasyonu izlenmeye devam eder, böylece
        "FORMASYON BOZULDU" uyarısı garanti gönderilir.
        """
        symbols = set(self.storage.all_watched_symbols())
        symbols |= {s.symbol for s in self.storage.list_open_signals()}
        if self.cfg.dynamic_top_n > 0:
            try:
                symbols |= set(
                    self.binance.top_symbols(
                        self.cfg.dynamic_top_n, set(self.cfg.exclude_bases)
                    )
                )
            except Exception:
                log.exception("Dinamik evren alınamadı")
        else:
            symbols |= set(self.cfg.default_symbols)
        return sorted(symbols)

    def _recipients(self, symbol: str) -> List[int]:
        """Bu sembol için bildirilecek chat_id'ler.

        Watchlist'i olanlar yalnızca kendi coinlerini; watchlist'i boş aktif
        aboneler dinamik evrenin tamamını alır.
        """
        watchers = set(self.storage.subscribers_watching(symbol))
        recipients = set(watchers)
        for chat_id in self.storage.active_subscribers():
            if not self.storage.list_watch(chat_id):
                recipients.add(chat_id)
        return list(recipients)

    def scan_once(self) -> None:
        symbols = self.universe()
        if not symbols:
            return
        fng_value = self.fng.fetch()
        log.info("Tarama: %s sembol", len(symbols))
        for symbol in symbols:
            try:
                self._scan_symbol(symbol, fng_value)
            except Exception:
                log.warning("Sembol taranamadı: %s", symbol, exc_info=True)
            time.sleep(0.15)  # hafif throttle — oran limiti

    def _scan_symbol(self, symbol: str, fng_value: Optional[int]) -> None:
        candles = self.binance.fetch_candles(symbol, limit=250)
        if len(candles) < 60:
            return
        ticker = self.binance.ticker_for(symbol)
        change_24h = ticker.change_pct if ticker else None
        premium = (
            self.binance.fetch_premium(symbol)
            if self.cfg.enable_futures_basis
            else None
        )
        analysis = analyze(
            symbol,
            candles,
            change_24h,
            fng_value,
            premium=premium,
            strong_threshold=self.cfg.alert_score_threshold,
        )
        self.storage.upsert_snapshot(
            symbol, analysis.composite, analysis.rating, analysis.price
        )
        self._lifecycle(symbol, analysis)

    def _lifecycle(self, symbol: str, analysis) -> None:
        open_signal = self.storage.get_open_signal(symbol)
        if open_signal is None:
            # Yeni sinyal: eşiği aştı mı?
            if analysis.composite >= self.cfg.alert_score_threshold:
                self.storage.upsert_open_signal(
                    symbol, analysis.price, analysis.rating, analysis.composite
                )
                self._broadcast(symbol, formatting.format_new_signal(analysis))
            return

        # Açık sinyal var — çıkış (formasyon bozuldu) koşulu mu?
        structural_break = analysis.rating == WEAK
        below_exit = analysis.composite < self.cfg.signal_exit_threshold
        if structural_break or below_exit:
            reason = (
                "yapısal kırılma (rating ZAYIF)"
                if structural_break
                else f"skor çıkış eşiğinin altında (`{analysis.composite:+.2f}`)"
            )
            msg = formatting.format_signal_exit(open_signal, analysis.price, reason)
            self.storage.delete_open_signal(symbol)
            self._broadcast(symbol, msg)
        else:
            # Hâlâ açık — skoru güncelle
            self.storage.upsert_open_signal(
                symbol, open_signal.entry_price, analysis.rating, analysis.composite
            )

    def _broadcast(self, symbol: str, text: str) -> None:
        for chat_id in self._recipients(symbol):
            try:
                self.notify(chat_id, text)
            except Exception:
                log.warning("Bildirim gönderilemedi: %s", chat_id, exc_info=True)
