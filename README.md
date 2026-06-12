# 📈 PriceMonitorX Bot

Bir kripto paranın **yükselme olasılığını**, teknik ve piyasa sinyallerini
derleyerek (konfluens skoru) özetleyen ve **Telegram** üzerinden raporlayan /
alarm veren, üretime hazır Python uygulaması.

> Sadece `TELEGRAM_TOKEN` gerekir — piyasa verisi **anahtarsız** çekilir
> (Binance public API + alternative.me Korku & Açgözlülük endeksi). Kutudan
> çıkar çıkmaz çalışır.

---

## ✨ Özellikler
- **9 sinyalin konfluensi:** Trend (SMA50/200), Golden/Death Cross, RSI, MACD,
  Hacim trendi, 30g Kırılım (destek/direnç), 24s Momentum, Fear & Greed,
  Vadeli/Spot Farkı (futures baz + funding).
- **Tek skor:** her sinyal `[-1,+1]` puan + ağırlık → ağırlıklı ortalama →
  `🟢 GÜÇLÜ / 🟡 NÖTR / 🔴 ZAYIF` + boğa olasılığı %. Tek sinyal değil,
  **sinyallerin hemfikir olması** belirleyici.
- **Takip listesi (watchlist):** kişiye özel, SQLite'ta kalıcı.
- **Otomatik sinyal yaşam döngüsü:** bir coin sinyal üretince (composite ≥
  `ALERT_SCORE_THRESHOLD`) otomatik **"YENİ SİNYAL"** raporu gelir; o coin "açık
  sinyal" olarak izlenir. Formasyonu bozulunca (GÜÇLÜ'den çıkış + yapısal
  kırılma ya da skorun `SIGNAL_EXIT_THRESHOLD` altına düşmesi) otomatik
  **"FORMASYON BOZULDU"** raporu (giriş fiyatına göre % değişimle) gelir.
- **Veri-bazlı stop önerisi:** 2×ATR ile.

## 🏛️ Mimari
```
crypto_signals/
├── config.py      # env doğrulama (fail-fast)
├── storage.py     # SQLite repository (abone, watchlist, snapshot, alarm state)
├── indicators.py  # saf fonksiyonlar: SMA / EMA / RSI / MACD / ATR (bağımlılıksız)
├── providers.py   # Binance (OHLCV+24s+vadeli prim) + alternative.me adapter'ları
├── signals.py     # her sinyali verdict + ağırlığa çevirir → kompozit skor (engine)
├── formatting.py  # rapor → Telegram Markdown
├── scheduler.py   # periyodik tarama + rating geçişinde alarm (ayrı thread)
└── bot.py         # Telegram handler'ları + entrypoint
crypto_bot.py      # kök başlatıcı
tests/             # saf indikatör + skor birim testleri
```

| Katman | Karar | Neden |
|---|---|---|
| **DB** | SQLite + ince repository | Sıfır bağımlılık; arayüz sayesinde Postgres'e geçiş tek dosya |
| **API** | Provider adapter deseni | Yeni borsa = yeni adapter; çekirdek değişmez |
| **UI** | Telegram persistent keyboard + komutlar | Sunucusuz arayüz, tek dokunuşla analiz |
| **State** | Kalıcı state SQLite'ta, scheduler ayrı thread'de | Restart'ta watchlist kaybolmaz; alarm sadece geçişte |

## 🚀 Çalıştırma
```bash
pip install -r requirements.txt
cp .env.example .env          # TELEGRAM_TOKEN'ı doldur
python crypto_bot.py
pytest                        # testler
```

## 💬 Komutlar
| Komut | Açıklama |
|---|---|
| `/sinyal BTC` | Anlık sinyal raporu |
| `/radar` | Son taramadaki en güçlü boğa sinyalleri (skora göre sıralı) |
| `/aktif` | Açık (izlenen) sinyaller |
| `/ekle SOL` · `/sil SOL` | Takip listesi yönetimi |
| `/liste` | Watchlist özeti (skora göre sıralı) |
| `/korku` | Piyasa Korku & Açgözlülük endeksi |
| `/abonelik_iptal` | Otomatik alarmları kapat |

## 🌐 Hangi coinler taranır?
- **Dinamik evren (varsayılan):** watchlist'i **boş** kullanıcılar için bot,
  Binance 24s hacmine göre **ilk `DYNAMIC_TOP_N` coin'i** (vars. **150**) her
  taramada yeniden seçip tarar. Stablecoin/fiat çiftleri elenir; `EXCLUDE_BASES`
  ile ek hariç tutma yapılabilir.
- **Kişisel watchlist:** `/ekle`–`/sil` ile liste tanımlayan kullanıcı yalnızca
  kendi coinlerini izler.
- `DYNAMIC_TOP_N=0` → dinamik mod kapanır, `DEFAULT_SYMBOLS` kullanılır.

Tek toplu ticker çağrısı hem top-N seçimi hem 24s momentum için kullanılır
(coin başına ekstra istek yok); büyük taramada hız limiti için hafif throttle var.

## ⚙️ Ortam Değişkenleri
| Değişken | Zorunlu | Varsayılan | Açıklama |
|---|---|---|---|
| `TELEGRAM_TOKEN` | ✅ | — | BotFather token'ı |
| `DYNAMIC_TOP_N` | ➖ | `150` | Watchlist'i boş olanlar için: hacme göre ilk N coin (0=kapalı) |
| `EXCLUDE_BASES` | ➖ | — | Dinamik evrenden hariç tut (ör. `PEPE,SHIB`) |
| `DEFAULT_SYMBOLS` | ➖ | `BTC,ETH,SOL` | `DYNAMIC_TOP_N=0` ise sabit liste |
| `ALERT_SCORE_THRESHOLD` | ➖ | `0.40` | Yeni sinyal (giriş) eşiği — 🟢 GÜÇLÜ sınırı |
| `SIGNAL_EXIT_THRESHOLD` | ➖ | `0.20` | Formasyon bozuldu (çıkış) eşiği — histerezis |
| `QUOTE_ASSET` | ➖ | `USDT` | İşlem çifti karşılığı |
| `SCAN_INTERVAL_MIN` | ➖ | `15` | Periyodik tarama sıklığı (dk) |
| `ENABLE_FUTURES_BASIS` | ➖ | `true` | Vadeli/Spot farkı sinyali (futures erişilemezse otomatik atlanır) |
| `DB_PATH` | ➖ | `crypto_signals.db` | SQLite dosya yolu |

## ☁️ Deploy (Render)
Background Worker olarak `python crypto_bot.py`. `PORT` tanımlıysa otomatik
health endpoint açılır, böylece Web Service olarak da çalışır.

## ⚠️ Sorumluluk Reddi
Bu yazılım **yatırım tavsiyesi değildir**. Sinyaller olasılık gösterir, garanti
vermez. Kripto piyasaları yüksek risklidir. Kendi araştırmanı yap (DYOR).

## 📄 Lisans
MIT — bkz. [LICENSE](LICENSE).
