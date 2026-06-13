"""Telegram handler'ları + entrypoint.

`main()` konfigürasyonu doğrular, bağımlılıkları kurar, scheduler'ı başlatır ve
botu polling moduna sokar. `PORT` tanımlıysa hafif bir health endpoint açılır
(Render Web Service uyumu).
"""
from __future__ import annotations

import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

import telebot
from telebot import types

from . import formatting
from .config import Config
from .providers import BinanceProvider, FearGreedProvider
from .scheduler import Scheduler
from .signals import Context, analyze, evaluate, market_regime_score
from .storage import Storage

log = logging.getLogger(__name__)

WELCOME = (
    "👋 *PriceMonitorX*'e hoş geldin!\n\n"
    "Kripto paraların yükselme olasılığını 9 sinyalin konfluensiyle özetlerim.\n\n"
    "• `/liste` — çekirdek takip listen (skorlarıyla)\n"
    "• `/check` — ilk 100 coinde fırsat tara, radara ekle\n"
    "• `/radar` — radarındaki fırsatlar\n"
    "• `/degerlendir PEPE` — almadan önce soğuk ikinci görüş (anti-FOMO)\n"
    "• `/sinyal BTC` — tek coin anlık rapor\n"
    "• `/ekle SOL` · `/sil SOL` — listeyi düzenle\n"
    "• `/korku` — Korku & Açgözlülük endeksi\n"
    "• `/abonelik_iptal` — uyarıları kapat\n\n"
    "Liste ve radardaki coinlerde formasyon bozulunca ya da 2×ATR stop kırılınca "
    "otomatik uyarı gelir.\n\n"
    "_Yatırım tavsiyesi değildir. DYOR._"
)


def _keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("/liste", "/radar", "/check")
    kb.row("/korku", "/aktif")
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

    _COMPUTE = object()  # _analyze için "btc rejimini sen hesapla" işareti

    def _btc_regime():
        try:
            return market_regime_score(binance.fetch_candles("BTC", limit=250).closes)
        except Exception:
            return None

    def _build(base: str, btc_regime=_COMPUTE):
        """(analysis, candles, ctx) döner; veri yetersizse None."""
        candles = binance.fetch_candles(base, limit=250)
        if len(candles) < 60:
            return None
        ticker = binance.ticker_for(base)
        change = ticker.change_pct if ticker else None
        quote_volume = ticker.quote_volume if ticker else None
        premium = binance.fetch_premium(base) if cfg.enable_futures_basis else None
        try:
            weekly = binance.fetch_candles(base, interval="1w", limit=60).closes
        except Exception:
            weekly = None
        br = _btc_regime() if btc_regime is _COMPUTE else btc_regime
        ctx = Context(
            change_pct_24h=change,
            fear_greed=fng.fetch(),
            premium=premium,
            weekly_closes=weekly,
            btc_regime=None if base == "BTC" else br,
            quote_volume=quote_volume,
            min_quote_volume=cfg.min_quote_volume,
        )
        a = analyze(base, candles, ctx, strong_threshold=cfg.alert_score_threshold)
        return a, candles, ctx

    def _analyze(base: str, btc_regime=_COMPUTE):
        built = _build(base, btc_regime)
        return built[0] if built else None

    @bot.message_handler(commands=["start", "yardim", "help"])
    def on_start(message):
        storage.add_subscriber(message.chat.id)
        # İlk girişte çekirdek listeyi otomatik kur
        if not storage.list_watch(message.chat.id):
            for sym in cfg.default_symbols:
                storage.add_watch(message.chat.id, sym)
        reply(message, WELCOME)

    @bot.message_handler(commands=["sinyal"])
    def on_signal(message):
        storage.add_subscriber(message.chat.id)
        base = _arg(message)
        if not base:
            reply(message, "Kullanım: `/sinyal BTC`")
            return
        try:
            a = _analyze(base)
            if a is None:
                reply(message, f"`{base}` için yeterli veri yok.")
                return
            reply(message, formatting.format_analysis(a))
        except Exception:
            log.exception("/sinyal hatası")
            reply(message, f"`{base}` analiz edilemedi. Sembolü kontrol edip tekrar dene.")

    @bot.message_handler(commands=["degerlendir", "sor"])
    def on_evaluate(message):
        storage.add_subscriber(message.chat.id)
        base = _arg(message)
        if not base:
            reply(message, "Kullanım: `/degerlendir PEPE` — almayı düşündüğün coini sor, soğuk bir görüş alırım.")
            return
        try:
            built = _build(base)
            if built is None:
                reply(message, f"`{base}` için yeterli veri yok.")
                return
            a, candles, ctx = built
            ev = evaluate(a, candles, ctx)
            reply(message, formatting.format_evaluation(base, a, ev))
        except Exception:
            log.exception("/degerlendir hatası")
            reply(message, f"`{base}` değerlendirilemedi. Sembolü kontrol edip tekrar dene.")

    @bot.message_handler(commands=["radar"])
    def on_radar(message):
        storage.add_subscriber(message.chat.id)
        symbols = storage.list_radar(message.chat.id)
        snaps = storage.snapshots_for(symbols)
        reply(message, formatting.format_radar(snaps, symbols))

    @bot.message_handler(commands=["check"])
    def on_check(message):
        storage.add_subscriber(message.chat.id)
        chat_id = message.chat.id
        bot.send_message(chat_id, "🔍 İlk 100 coin taranıyor, ~30 sn sürebilir…")
        tracked = set(storage.list_watch(chat_id)) | set(storage.list_radar(chat_id))
        try:
            top = binance.top_symbols(cfg.check_top_n, set(cfg.exclude_bases))
        except Exception:
            log.exception("/check top listesi alınamadı")
            reply(message, "Tarama listesi alınamadı, birazdan tekrar dene.")
            return
        br = _btc_regime()  # tüm tarama için bir kez
        candidates = []
        for sym in top:
            if sym in tracked:
                continue
            try:
                a = _analyze(sym, btc_regime=br)
                if a is not None and a.composite >= cfg.check_score_threshold:
                    candidates.append(a)
            except Exception:
                continue
            time.sleep(0.1)  # hafif throttle
        candidates.sort(key=lambda a: a.composite, reverse=True)
        candidates = candidates[:15]
        markup = None
        if candidates:
            markup = types.InlineKeyboardMarkup()
            for a in candidates:
                markup.add(
                    types.InlineKeyboardButton(
                        f"➕ {a.symbol}  (%{a.bull_prob:.0f})",
                        callback_data=f"radar_add:{a.symbol}",
                    )
                )
        bot.send_message(chat_id, formatting.format_check(candidates), reply_markup=markup)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("radar_add:"))
    def on_radar_add(call):
        sym = call.data.split(":", 1)[1]
        chat_id = call.message.chat.id
        storage.add_subscriber(chat_id)
        storage.add_radar(chat_id, sym)
        try:
            a = _analyze(sym)
            if a is not None:
                storage.upsert_snapshot(sym, a.composite, a.rating, a.price)
                storage.upsert_open_signal(
                    sym, a.price, a.rating, a.composite, stop=a.stop_suggestion
                )
                stop_txt = (
                    f" · 🛑 `{formatting._fmt_price(a.stop_suggestion)}`"
                    if a.stop_suggestion is not None
                    else ""
                )
                bot.answer_callback_query(call.id, f"{sym} radara eklendi ✅")
                bot.send_message(
                    chat_id,
                    f"📡 *{sym}* radara eklendi · giriş `{formatting._fmt_price(a.price)}`{stop_txt}",
                )
            else:
                bot.answer_callback_query(call.id, f"{sym} eklendi (veri yetersiz)")
        except Exception:
            log.exception("radar_add hatası")
            bot.answer_callback_query(call.id, f"{sym} eklendi, analiz sonra güncellenecek")

    @bot.callback_query_handler(func=lambda c: c.data.startswith("radar_del:"))
    def on_radar_del(call):
        sym = call.data.split(":", 1)[1]
        chat_id = call.message.chat.id
        removed = storage.remove_radar(chat_id, sym)
        if not storage.subscribers_radar(sym) and not storage.subscribers_watching(sym):
            storage.delete_open_signal(sym)  # kimse takip etmiyorsa kaydı temizle
        bot.answer_callback_query(
            call.id, f"{sym} radardan çıkarıldı" if removed else f"{sym} radarda değil"
        )
        if removed:
            bot.send_message(chat_id, f"➖ *{sym}* radardan çıkarıldı.")

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
    storage = Storage(cfg.db_path, cfg.database_url or None)
    log.info(
        "Veri deposu: %s", "PostgreSQL (kalıcı)" if storage.is_pg else "SQLite (yerel)"
    )
    binance = BinanceProvider(cfg.quote_asset)
    fng = FearGreedProvider()
    bot = build_bot(cfg, storage, binance, fng)

    def notify(chat_id: int, text: str, remove_symbol: Optional[str] = None) -> None:
        markup = None
        if remove_symbol:
            markup = types.InlineKeyboardMarkup()
            markup.add(
                types.InlineKeyboardButton(
                    f"➖ {remove_symbol} radardan çıkar",
                    callback_data=f"radar_del:{remove_symbol}",
                )
            )
        bot.send_message(chat_id, text, reply_markup=markup)

    scheduler = Scheduler(cfg, storage, binance, fng, notify)
    scheduler.start()

    if cfg.port:
        _start_health_server(cfg.port)

    # Webhook varsa temizle (polling ile çakışmasın)
    try:
        bot.remove_webhook()
    except Exception:
        pass

    log.info("Bot polling başlıyor…")
    # NOT: skip_pending=True KULLANMIYORUZ. Render zero-downtime deploy'da eski
    # instance birkaç saniye hâlâ polling yaparken Telegram 409 (Conflict) döner.
    # skip_pending'in çağırdığı ön getUpdates bu hatayı koruma döngüsünden ÖNCE,
    # korumasız fırlatır → süreç çöker → restart döngüsü. infinity_polling'in
    # kendi döngüsü 409'u yakalayıp yeniden dener; eski instance ölünce toparlanır.
    while True:
        try:
            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=30,
                logger_level=logging.WARNING,
            )
            break  # temiz çıkış (stop çağrıldıysa)
        except Exception:
            log.exception("Polling beklenmedik şekilde durdu; 5 sn sonra tekrar")
            time.sleep(5)


if __name__ == "__main__":  # pragma: no cover
    main()
