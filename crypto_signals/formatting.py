"""Rapor → Telegram Markdown biçimlendirme."""
from __future__ import annotations

from typing import List

from .signals import Analysis
from .storage import OpenSignal, Snapshot


def _fmt_price(price: float) -> str:
    if price >= 1000:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:.4f}"
    return f"{price:.8f}".rstrip("0").rstrip(".")


def bull_pct(score: float) -> float:
    """Kompozit skoru (-1..+1) boğa olasılığı yüzdesine (0-100) çevirir."""
    return (score + 1.0) / 2.0 * 100.0


def format_analysis(a: Analysis) -> str:
    lines = [
        f"*{a.symbol}* — {a.rating}",
        f"Fiyat: `{_fmt_price(a.price)}`",
        f"Boğa olasılığı: *%{a.bull_prob:.0f}*",
        "",
        "*Sinyaller:*",
    ]
    for v in a.verdicts:
        if not v.available:
            lines.append(f"➖ {v.name}: _{v.detail}_ (skora katılmadı)")
            continue
        icon = "🟢" if v.score > 0.15 else "🔴" if v.score < -0.15 else "⚪️"
        lines.append(f"{icon} {v.name}: *%{bull_pct(v.score):.0f}* — {v.detail}")
    if a.stop_suggestion is not None:
        lines += ["", f"🛑 Stop önerisi (2×ATR): `{_fmt_price(a.stop_suggestion)}`"]
    lines += ["", "_Yatırım tavsiyesi değildir. DYOR._"]
    return "\n".join(lines)


def format_new_signal(a: Analysis) -> str:
    return (
        f"🚨 *YENİ SİNYAL* — *{a.symbol}*\n"
        f"{a.rating}  ·  boğa olasılığı *%{a.bull_prob:.0f}*\n"
        f"Giriş fiyatı: `{_fmt_price(a.price)}`\n"
        + (
            f"🛑 Stop (2×ATR): `{_fmt_price(a.stop_suggestion)}`\n"
            if a.stop_suggestion is not None
            else ""
        )
        + "_Yatırım tavsiyesi değildir. DYOR._"
    )


def format_signal_exit(signal: OpenSignal, current_price: float, reason: str) -> str:
    if signal.entry_price:
        change = (current_price - signal.entry_price) / signal.entry_price * 100.0
        change_txt = f"{change:+.2f}%"
    else:
        change_txt = "—"
    return (
        f"⚠️ *FORMASYON BOZULDU* — *{signal.symbol}*\n"
        f"Giriş: `{_fmt_price(signal.entry_price)}` → Şimdi: `{_fmt_price(current_price)}`  "
        f"(*{change_txt}*)\n"
        f"Sebep: {reason}"
    )


def format_radar(snapshots: List[Snapshot]) -> str:
    if not snapshots:
        return "Henüz tarama yapılmadı. Birazdan tekrar deneyin."
    lines = ["📡 *RADAR* — en güçlü boğa sinyalleri:", ""]
    for i, s in enumerate(snapshots, 1):
        lines.append(
            f"{i}. *{s.symbol}* {s.rating} · *%{bull_pct(s.score):.0f}* · `{_fmt_price(s.price)}`"
        )
    return "\n".join(lines)


def format_active(signals: List[OpenSignal]) -> str:
    if not signals:
        return "Şu an açık (izlenen) sinyal yok."
    lines = ["🎯 *AÇIK SİNYALLER:*", ""]
    for s in signals:
        line = (
            f"• *{s.symbol}* {s.rating} · giriş `{_fmt_price(s.entry_price)}` "
            f"· *%{bull_pct(s.score):.0f}*"
        )
        if s.stop is not None:
            line += f" · 🛑 `{_fmt_price(s.stop)}`"
        lines.append(line)
    return "\n".join(lines)


def format_watchlist(snapshots: List[Snapshot], symbols: List[str]) -> str:
    if not symbols:
        return (
            "Takip listen boş. `/ekle BTC` ile coin ekleyebilirsin.\n"
            "Boş listede bot otomatik olarak en yüksek hacimli coinleri tarar."
        )
    # snapshots zaten skora göre büyükten küçüğe sıralı gelir.
    lines = ["📋 *TAKİP LİSTEN* (skora göre):", ""]
    for s in snapshots:
        lines.append(f"• *{s.symbol}* {s.rating} · *%{bull_pct(s.score):.0f}* · `{_fmt_price(s.price)}`")
    scored = {s.symbol for s in snapshots}
    pending = [sym for sym in symbols if sym not in scored]
    if pending:
        if snapshots:
            lines.append("")
        lines.append("_Henüz taranmadı (sıradaki taramada skorlanacak):_ " + ", ".join(pending))
    return "\n".join(lines)


def format_fear_greed(value: int) -> str:
    if value <= 25:
        label = "Aşırı Korku 😱"
    elif value <= 45:
        label = "Korku 😟"
    elif value <= 55:
        label = "Nötr 😐"
    elif value <= 75:
        label = "Açgözlülük 😎"
    else:
        label = "Aşırı Açgözlülük 🤑"
    return f"😨 *Korku & Açgözlülük Endeksi:* `{value}` — {label}"
