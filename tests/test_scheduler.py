"""Scheduler evren/yaşam döngüsü testleri — ağ gerektirmez (stub provider)."""
from crypto_signals.config import Config
from crypto_signals.scheduler import Scheduler
from crypto_signals.storage import Storage


class _StubBinance:
    """Sabit top-N döndüren, ağ kullanmayan sahte sağlayıcı."""

    def __init__(self, top):
        self._top = top

    def top_symbols(self, top_n, exclude_bases=None):
        return list(self._top)


class _StubFng:
    def fetch(self, **_):
        return 50


def _config(**kw):
    base = dict(telegram_token="123456:dummy", dynamic_top_n=3)
    base.update(kw)
    return Config(**base)


def _scheduler(storage, top):
    return Scheduler(_config(), storage, _StubBinance(top), _StubFng(), lambda c, t: None)


def test_universe_includes_dynamic_and_watchlist():
    st = Storage(":memory:")
    st.add_subscriber(1)
    st.add_watch(1, "FOO")
    sched = _scheduler(st, ["AAA", "BBB"])
    universe = sched.universe()
    assert {"AAA", "BBB", "FOO"} <= set(universe)


def test_open_signal_always_tracked_even_if_dropped_from_top_n():
    st = Storage(":memory:")
    # ZZZ sinyal vermiş (açık), ama artık top-N'de yok
    st.upsert_open_signal("ZZZ", entry_price=10.0, rating="🟢 GÜÇLÜ", score=0.5)
    sched = _scheduler(st, ["AAA", "BBB"])  # top-N içinde ZZZ yok
    assert "ZZZ" in sched.universe(), "açık sinyal evrende kalmalı (formasyon takibi)"


def test_dynamic_off_uses_default_symbols():
    st = Storage(":memory:")
    sched = Scheduler(
        _config(dynamic_top_n=0, default_symbols=["BTC", "ETH"]),
        st,
        _StubBinance([]),
        _StubFng(),
        lambda c, t: None,
    )
    assert {"BTC", "ETH"} <= set(sched.universe())
