"""PriceMonitorX — kripto konfluens sinyal botu paketi.

Modüller:
    config       env doğrulama (fail-fast)
    indicators   saf teknik göstergeler (SMA/EMA/RSI/MACD/ATR)
    providers    Binance + alternative.me adapter'ları
    signals      sinyalleri kompozit skora çeviren motor
    storage      SQLite repository
    formatting   Telegram Markdown raporları
    scheduler    periyodik tarama + alarm thread'i
    bot          Telegram handler'ları + entrypoint
"""

__all__ = ["__version__"]
__version__ = "1.0.0"
