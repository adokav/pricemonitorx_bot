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


def _bar(pct: float, width: int = 10) -> str:
    """Yüzdeyi mini ilerleme çubuğuna çevirir (örn. ▰▰▰▰▰▰▱▱▱▱)."""
    filled = max(0, min(width, round(pct / 100.0 * width)))
    return "▰" * filled + "▱" * (width - filled)


def _rating_icon(rating: str) -> str:
    """Rating metnindeki emojiyi (🟢/🟡/🔴) döndürür."""
    return rating.split()[0] if rating else "⚪️"


_REGIME_LABEL = {
    "TREND_UP": "📈 Yükseliş trendi",
    "TREND_DOWN": "📉 Düşüş trendi",
    "RANGE": "↔️ Yatay/sıkışma",
}


def format_analysis(a: Analysis) -> str:
    lines = [
        f"*{a.symbol}* — {a.rating}",
        f"Fiyat: `{_fmt_price(a.price)}`  ·  {_REGIME_LABEL.get(a.regime, a.regime)}",
        f"Boğa olasılığı: *%{a.bull_prob:.0f}*",
    ]
    for note in a.notes:
        lines.append(note)
    lines += ["", "*Sinyaller:*"]
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


def _format_tracked(
    title: str, snapshots: List[Snapshot], symbols: List[str], empty_msg: str
) -> str:
    if not symbols:
        return empty_msg
    strong = sum(1 for s in snapshots if s.score >= 0.40)
    weak = sum(1 for s in snapshots if s.score <= -0.40)
    neutral = len(snapshots) - strong - weak
    header = f"{title} · {len(symbols)} coin"
    if snapshots:
        header += f"\n🟢 {strong}  ·  🟡 {neutral}  ·  🔴 {weak}"
    lines = [header, ""]
    for s in snapshots:
        pct = bull_pct(s.score)
        lines.append(
            f"{_rating_icon(s.rating)} *{s.symbol}* — *%{pct:.0f}*\n"
            f"`{_bar(pct)}`  `{_fmt_price(s.price)}`"
        )
    scored = {s.symbol for s in snapshots}
    pending = [sym for sym in symbols if sym not in scored]
    if pending:
        lines.append("")
        lines.append("⏳ _Sırada (henüz taranmadı):_ " + ", ".join(pending))
    return "\n".join(lines)


def format_radar(snapshots: List[Snapshot], symbols: List[str]) -> str:
    return _format_tracked(
        "📡 *RADAR*",
        snapshots,
        symbols,
        "📡 *Radar boş.*\n\n`/check` ile fırsat taraması yapıp coin ekleyebilirsin.",
    )


def format_check(candidates) -> str:
    """candidates: skora göre sıralı Analysis listesi."""
    if not candidates:
        return (
            "🔍 İlk 100 coinde eşiği geçen yeni fırsat bulunamadı.\n"
            "Piyasa zayıf olabilir; sonra tekrar dene."
        )
    lines = ["🔍 *FIRSAT TARAMASI* — eşiği geçen coinler:", ""]
    for a in candidates:
        lines.append(
            f"{_rating_icon(a.rating)} *{a.symbol}* — *%{a.bull_prob:.0f}*  `{_bar(a.bull_prob)}`"
        )
    lines.append("")
    lines.append("_Aşağıdaki butonlarla radara ekleyebilirsin._")
    return "\n".join(lines)


_VERDICT_LABEL = {
    "AVOID": "⛔ UZAK DUR",
    "WAIT": "⏳ BEKLE",
    "CONSIDER": "✅ DEĞERLENDİRİLEBİLİR",
}
_CHECK_ICON = {"ok": "✅", "warn": "⚠️", "bad": "⛔"}


def format_evaluation(symbol: str, a: Analysis, ev) -> str:
    lines = [
        f"🧮 *DEĞERLENDİRME* — *{symbol}*",
        f"Karar: *{_VERDICT_LABEL.get(ev.verdict, ev.verdict)}* — {ev.headline}",
        f"Fiyat `{_fmt_price(a.price)}` · {_REGIME_LABEL.get(a.regime, a.regime)} · skor *%{a.bull_prob:.0f}*",
        "",
        "*Soğuk gerçekler:*",
    ]
    for c in ev.checks:
        lines.append(f"{_CHECK_ICON.get(c.status, '•')} {c.label}")
    risk_line = []
    if a.stop_suggestion is not None:
        risk_line.append(f"🛑 Stop `{_fmt_price(a.stop_suggestion)}`")
    if ev.target is not None:
        risk_line.append(f"🎯 Hedef `{_fmt_price(ev.target)}`")
    if ev.rr is not None:
        risk_line.append(f"R:R `{ev.rr:.1f}`")
    if risk_line:
        lines += ["", " · ".join(risk_line)]
    lines += ["", f"🧭 _{ev.note}_", "_Yatırım tavsiyesi değildir. DYOR._"]
    return "\n".join(lines)


def format_stop_hit(signal: OpenSignal, current_price: float) -> str:
    if signal.entry_price:
        change = (current_price - signal.entry_price) / signal.entry_price * 100.0
        change_txt = f"{change:+.2f}%"
    else:
        change_txt = "—"
    stop_txt = _fmt_price(signal.stop) if signal.stop is not None else "—"
    return (
        f"🛑 *STOP KIRILDI* — *{signal.symbol}*\n"
        f"Giriş: `{_fmt_price(signal.entry_price)}` → Şimdi: `{_fmt_price(current_price)}`  "
        f"(*{change_txt}*)\n"
        f"2×ATR stop (`{stop_txt}`) seviyesi kırıldı."
    )


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
    return _format_tracked(
        "📋 *TAKİP LİSTEN*",
        snapshots,
        symbols,
        "📋 *Takip listen boş.*\n\n`/ekle BTC` ile coin ekleyebilirsin.",
    )


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
