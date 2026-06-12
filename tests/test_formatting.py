"""Biçimlendirme testleri — watchlist sıralaması ve eksik skorlar."""
from crypto_signals import formatting as f
from crypto_signals.storage import Snapshot


def _snap(symbol, score, price=1.0):
    return Snapshot(symbol=symbol, score=score, rating="🟡 NÖTR", price=price, updated_at=0)


def test_watchlist_sorted_high_to_low_and_pending_at_bottom():
    # snapshots_for skora göre DESC döndürür; biçimlendirme bu sırayı korumalı
    snaps = [_snap("AAA", 0.62), _snap("BBB", 0.40), _snap("CCC", -0.10)]
    symbols = ["AAA", "BBB", "CCC", "ZZZ"]  # ZZZ henüz taranmamış (skorsuz)
    out = f.format_watchlist(snaps, symbols)
    # Sıra: AAA önce, sonra BBB, sonra CCC
    assert out.index("AAA") < out.index("BBB") < out.index("CCC")
    # Taranmamış coin en altta, "Henüz taranmadı" bölümünde
    assert "ZZZ" in out
    assert out.index("Henüz taranmadı") < out.index("ZZZ")
    assert out.index("CCC") < out.index("ZZZ")


def test_watchlist_all_pending_when_no_snapshots():
    out = f.format_watchlist([], ["BTC", "ETH"])
    assert "BTC" in out and "ETH" in out
    assert "Henüz taranmadı" in out


def test_empty_watchlist_message():
    out = f.format_watchlist([], [])
    assert "boş" in out.lower()
