"""Ortam değişkeni doğrulama — fail-fast.

`Config.from_env()` tek `TELEGRAM_TOKEN` dışında her şey için makul
varsayılanlar üretir; token yoksa anlaşılır bir hata fırlatır.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

try:  # .env varsa yükle (opsiyonel bağımlılık zaten requirements'ta)
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv yoksa sessizce devam
    pass


class ConfigError(RuntimeError):
    """Eksik/geçersiz konfigürasyon."""


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:  # pragma: no cover - kullanıcı hatası
        raise ConfigError(f"{name} bir tam sayı olmalı (alınan: {raw!r})") from exc


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:  # pragma: no cover
        raise ConfigError(f"{name} bir ondalık sayı olmalı (alınan: {raw!r})") from exc


def _get_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return list(default)
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Config:
    telegram_token: str
    quote_asset: str = "USDT"
    dynamic_top_n: int = 150
    default_symbols: list[str] = field(default_factory=lambda: ["BTC", "ETH", "SOL"])
    exclude_bases: list[str] = field(default_factory=list)
    alert_score_threshold: float = 0.40
    signal_exit_threshold: float = 0.20
    scan_interval_min: int = 15
    db_path: str = "crypto_signals.db"
    port: int | None = None

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("TELEGRAM_TOKEN", "").strip()
        if not token:
            raise ConfigError(
                "TELEGRAM_TOKEN tanımlı değil. BotFather'dan aldığınız token'ı "
                "ortam değişkeni olarak ayarlayın (Render: Environment sekmesi)."
            )
        port_raw = os.getenv("PORT")
        port = int(port_raw) if port_raw and port_raw.strip() else None
        return cls(
            telegram_token=token,
            quote_asset=os.getenv("QUOTE_ASSET", "USDT").strip().upper() or "USDT",
            dynamic_top_n=_get_int("DYNAMIC_TOP_N", 150),
            default_symbols=_get_list("DEFAULT_SYMBOLS", ["BTC", "ETH", "SOL"]),
            exclude_bases=_get_list("EXCLUDE_BASES", []),
            alert_score_threshold=_get_float("ALERT_SCORE_THRESHOLD", 0.40),
            signal_exit_threshold=_get_float("SIGNAL_EXIT_THRESHOLD", 0.20),
            scan_interval_min=_get_int("SCAN_INTERVAL_MIN", 15),
            db_path=os.getenv("DB_PATH", "crypto_signals.db").strip() or "crypto_signals.db",
            port=port,
        )
