"""Telegram handler'ları + entrypoint.

`main()` konfigürasyonu doğrular, bağımlılıkları kurar, scheduler'ı başlatır ve
botu polling moduna sokar. `PORT` tanımlıysa hafif bir health endpoint açılır
(Render Web Service uyumu).
"""
from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

import telebot
from telebot import types

from . import formatting
from .config import Config
from .providers import BinanceProvider, FearGreedProvider
from .scheduler import Scheduler
from .signals import analyze
from .storage import Storage

log = logging.getLogger(__name__)

WELCOME = (
    "👋 *PriceMonitorX*'e hoş geldin!\n\n"
    "Kripto paraların yükselme olasılığını 8 sinyalin konfluensiyle özetlerim.\n\n"
    "• `/sinyal BTC` — anlık rapor\n"
    "• `/radar` — en güçlü boğa sinyalleri\n"
    "• `/aktif` — açık sinyaller\n"
    "• `/ekle SOL` · `/sil SOL` — takip listesi\n"
    "• `/liste` — watchlist özeti\n"
    "• `/korku` — Korku & Açgözlülük endeksi\n"
    "• `/abonelik_iptal` — otomatik alarmları kapat\n\n"
    "_Yatırım tavsiyesi değildir. DYOR._"
)


def _keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("/radar", "/aktif", "/liste")
    kb.row("/korku")
    return kb


def _arg(message) -> Optional[str]:
    parts = message.text.strip().split(maxsplit=1)
    return parts[1].strip().upper() if len(parts) > 1 else None


def build_bot(
    cfg: Config,
    storage: Storage,
    binance: BinanceProvider,
    fng: FearGreedProvider,
) -> telebot.TeleBot:
    bot = telebot.TeleBot(cfg.telegram_token, parse_mode="Markdown")

    def reply(message, text: str) -> None:
        bot.send_message(message.chat.id, text, reply_markup=_keyboard())

    @bot.message_handler(commands=["start", "yardim", "help"])
    def on_start(message):
        storage.add_subscriber(message.chat.id)
        reply(message, WELCOME)

    @bot.message_handler(commands=["sinyal"])
    def on_signal(message):
        storage.add_subscriber(message.chat.id)
        base = _arg(message)
        if not base:
            reply(message, "Kullanım: `/sinyal BTC`")
            return
        try:
            candles = binance.fetch_candles(base, limit=250)
            if len(candles) < 60:
                reply(message, f"`{base}` için yeterli veri yok.")
                return
            ticker = binance.ticker_for(base)
            change = ticker.change_pct if ticker else None
            a = analyze(base, candles, change, fng.fetch(), cfg.alert_score_threshold)
            reply(message, formatting.format_analysis(a))
        except Exception:
            log.exception("/sinyal hatası")
            reply(message, f"`{base}` analiz edilemedi. Sembolü kontrol edip tekrar dene.")

    @bot.message_handler(commands=["radar"])
    def on_radar(message):
        storage.add_subscriber(message.chat.id)
        reply(message, formatting.format_radar(storage.top_snapshots(10)))

    @bot.message_handler(commands=["aktif"])
    def on_active(message):
        reply(message, formatting.format_active(storage.list_open_signals()))

    @bot.message_handler(commands=["ekle"])
    def on_add(message):
        base = _arg(message)
        if not base:
            reply(message, "Kullanım: `/ekle SOL`")
            return
        storage.add_subscriber(message.chat.id)
        storage.add_watch(message.chat.id, base)
        reply(message, f"✅ `{base}` takip listene eklendi.")

    @bot.message_handler(commands=["sil"])
    def on_remove(message):
        base = _arg(message)
        if not base:
            reply(message, "Kullanım: `/sil SOL`")
            return
        removed = storage.remove_watch(message.chat.id, base)
        reply(message, f"🗑️ `{base}` silindi." if removed else f"`{base}` listende yok.")

    @bot.message_handler(commands=["liste"])
    def on_list(message):
        symbols = storage.list_watch(message.chat.id)
        snaps = storage.snapshots_for(symbols)
        reply(message, formatting.format_watchlist(snaps, symbols))

    @bot.message_handler(commands=["korku"])
    def on_fng(message):
        value = fng.fetch()
        if value is None:
            reply(message, "Korku & Açgözlülük endeksi şu an alınamıyor.")
        else:
            reply(message, formatting.format_fear_greed(value))

    @bot.message_handler(commands=["abonelik_iptal"])
    def on_unsub(message):
        storage.deactivate_subscriber(message.chat.id)
        reply(message, "🔕 Otomatik alarmlar kapatıldı. `/start` ile tekrar açabilirsin.")

    return bot


def _start_health_server(port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):  # sessiz
            pass

    server = HTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, name="health", daemon=True).start()
    log.info("Health endpoint :%s üzerinde dinleniyor.", port)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = Config.from_env()  # token yoksa burada fail-fast
    storage = Storage(cfg.db_path)
    binance = BinanceProvider(cfg.quote_asset)
    fng = FearGreedProvider()
    bot = build_bot(cfg, storage, binance, fng)

    def notify(chat_id: int, text: str) -> None:
        bot.send_message(chat_id, text)

    scheduler = Scheduler(cfg, storage, binance, fng, notify)
    scheduler.start()

    if cfg.port:
        _start_health_server(cfg.port)

    log.info("Bot polling başlıyor…")
    bot.infinity_polling(skip_pending=True, timeout=30)


if __name__ == "__main__":  # pragma: no cover
    main()
