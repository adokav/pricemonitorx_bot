"""Biçimlendirme testleri — watchlist sıralaması ve eksik skorlar."""
from crypto_signals import formatting as f
from crypto_signals.storage import OpenSignal, Snapshot


def test_active_shows_stop_when_present():
    sig = OpenSignal("BTC", entry_price=100.0, rating="🟢 GÜÇLÜ", score=0.5, created_at=0, stop=92.0)
    out = f.format_active([sig])
    assert "BTC" in out and "🛑" in out and "92" in out


def test_active_omits_stop_when_missing():
    sig = OpenSignal("ETH", entry_price=50.0, rating="🟢 GÜÇLÜ", score=0.5, created_at=0)
    out = f.format_active([sig])
    assert "ETH" in out and "🛑" not in out


def _snap(symbol, score, price=1.0):
    return Snapshot(symbol=symbol, score=score, rating="🟡 NÖTR", price=price, updated_at=0)


def test_watchlist_sorted_high_to_low_and_pending_at_bottom():
    # snapshots_for skora göre DESC döndürür; biçimlendirme bu sırayı korumalı
    snaps = [_snap("AAA", 0.62), _snap("BBB", 0.40), _snap("CCC", -0.10)]
    symbols = ["AAA", "BBB", "CCC", "ZZZ"]  # ZZZ henüz taranmamış (skorsuz)
    out = f.format_watchlist(snaps, symbols)
    # Sıra: AAA önce, sonra BBB, sonra CCC
    assert out.index("AAA") < out.index("BBB") < out.index("CCC")
    # Taranmamış coin en altta, "Sırada" bölümünde
    assert "ZZZ" in out
    assert out.index("Sırada") < out.index("ZZZ")
    assert out.index("CCC") < out.index("ZZZ")


def test_watchlist_all_pending_when_no_snapshots():
    out = f.format_watchlist([], ["BTC", "ETH"])
    assert "BTC" in out and "ETH" in out
    assert "Sırada" in out


def test_empty_watchlist_message():
    out = f.format_watchlist([], [])
    assert "boş" in out.lower()
