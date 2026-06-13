"""Scheduler evren/yaşam döngüsü testleri — ağ gerektirmez (stub provider)."""
from crypto_signals.config import Config
from crypto_signals.scheduler import Scheduler
from crypto_signals.storage import Storage


class _StubBinance:
    def top_symbols(self, top_n, exclude_bases=None):
        return []


class _StubFng:
    def fetch(self, **_):
        return 50


def _config(**kw):
    base = dict(telegram_token="123456:dummy")
    base.update(kw)
    return Config(**base)


def _scheduler(storage):
    return Scheduler(_config(), storage, _StubBinance(), _StubFng(), lambda c, t, r=None: None)


def test_universe_is_watchlist_plus_radar_plus_open():
    st = Storage(":memory:")
    st.add_subscriber(1)
    st.add_watch(1, "FOO")
    st.add_radar(1, "BAR")
    st.upsert_open_signal("ZZZ", 10.0, "🟢 GÜÇLÜ", 0.5)
    universe = set(_scheduler(st).universe())
    assert {"FOO", "BAR", "ZZZ"} <= universe


def test_universe_excludes_untracked_coins():
    st = Storage(":memory:")
    st.add_subscriber(1)
    st.add_watch(1, "FOO")
    # Dinamik top-N artık periyodik taramaya girmez
    assert set(_scheduler(st).universe()) == {"FOO"}


def test_open_signal_always_tracked():
    st = Storage(":memory:")
    st.upsert_open_signal("ZZZ", entry_price=10.0, rating="🟢 GÜÇLÜ", score=0.5)
    assert "ZZZ" in _scheduler(st).universe()


def test_recipients_union_of_watchers_and_radar():
    st = Storage(":memory:")
    st.add_subscriber(1)
    st.add_subscriber(2)
    st.add_watch(1, "BTC")
    st.add_radar(2, "BTC")
    recipients = set(_scheduler(st)._recipients("BTC"))
    assert recipients == {1, 2}
