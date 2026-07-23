"""Webull market-data adapter: tolerant bar/quote mapping from a fake SDK
client, the dedicated intraday-provider factory + cache, and the short-TTL
Entry Check cache. All offline - no real SDK, no network. The concrete SDK
method/field names are exercised via an injected fake client (the one binding
that must be re-verified against the installed SDK version is documented in
catalystiq/providers/webull_market_data.py)."""
from __future__ import annotations

import datetime as dt

import pytest

from catalystiq.providers.webull_market_data import WebullMarketDataProvider


class _FakeResp:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code
        self.text = "err"

    def json(self):
        return self._body


class _FakeMarketData:
    """Mimics data_client.market_data with Webull-ish payloads."""

    def __init__(self, *, bars=None, snapshot=None, status=200):
        self._bars = bars if bars is not None else []
        self._snapshot = snapshot
        self._status = status
        self.calls: list[dict] = []

    def get_history_bar(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResp({"data": self._bars}, status_code=self._status)

    def get_snapshot(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResp(self._snapshot, status_code=self._status)


class _FakeDataClient:
    def __init__(self, market_data):
        self.market_data = market_data


def _provider(market_data) -> WebullMarketDataProvider:
    return WebullMarketDataProvider("k", "s", "us", client=_FakeDataClient(market_data))


def _epoch(y, mo, d, h, mi) -> int:
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=dt.timezone.utc).timestamp())


# --- intraday bar mapping ---------------------------------------------------


def test_get_intraday_ohlcv_maps_and_sorts_bars():
    rows = [
        {"timestamp": _epoch(2026, 7, 20, 13, 35), "open": 10, "high": 11, "low": 9.5,
         "close": 10.5, "volume": 1000},
        {"timestamp": _epoch(2026, 7, 20, 13, 30), "open": 9.9, "high": 10.2, "low": 9.8,
         "close": 10.0, "volume": 800},
    ]
    md = _FakeMarketData(bars=rows)
    p = _provider(md)
    bars = p.get_intraday_ohlcv("aapl", interval="5m", days=20)
    assert len(bars) == 2
    # Sorted ascending by timestamp; fields mapped.
    assert bars[0].timestamp < bars[1].timestamp
    assert bars[0].open == 9.9 and bars[1].close == 10.5
    assert bars[0].timestamp.tzinfo is not None
    # The interval mapped to Webull's m5 timespan; symbol upper-cased.
    assert md.calls[0]["timespan"] == "m5"
    assert md.calls[0]["symbol"] == "AAPL"


def test_intraday_tolerates_short_field_names_and_ms_timestamps():
    rows = [
        {"t": _epoch(2026, 7, 20, 13, 30) * 1000, "o": 5, "h": 6, "l": 4, "c": 5.5, "v": 10},
    ]
    p = _provider(_FakeMarketData(bars=rows))
    bars = p.get_intraday_ohlcv("X", interval="5m")
    assert len(bars) == 1
    assert bars[0].close == 5.5 and bars[0].volume == 10
    assert bars[0].timestamp == dt.datetime(2026, 7, 20, 13, 30, tzinfo=dt.timezone.utc)


def test_intraday_skips_unparseable_rows_never_fabricates():
    rows = [
        {"timestamp": _epoch(2026, 7, 20, 13, 30), "open": 5, "high": 6, "low": 4, "close": 5.5,
         "volume": 10},
        {"open": None, "high": None},  # junk row -> skipped, not zero-filled
    ]
    p = _provider(_FakeMarketData(bars=rows))
    bars = p.get_intraday_ohlcv("X")
    assert len(bars) == 1


def test_unsupported_interval_raises():
    from catalystiq.providers.market_data import MarketDataError

    p = _provider(_FakeMarketData())
    with pytest.raises(MarketDataError):
        p.get_intraday_ohlcv("X", interval="7m")


def test_non_200_status_raises_market_data_error():
    from catalystiq.providers.market_data import MarketDataError

    p = _provider(_FakeMarketData(bars=[], status=429))
    with pytest.raises(MarketDataError):
        p.get_intraday_ohlcv("X")


# --- quote mapping ----------------------------------------------------------


def test_get_quote_maps_last_price():
    md = _FakeMarketData(snapshot={"symbol": "AAPL", "close": 191.23, "preClose": 190.0})
    p = _provider(md)
    q = p.get_quote("aapl")
    assert q.symbol == "AAPL" and q.price == 191.23 and q.previous_close == 190.0


def test_get_quote_missing_price_raises():
    from catalystiq.providers.market_data import MarketDataError

    p = _provider(_FakeMarketData(snapshot={"symbol": "AAPL"}))
    with pytest.raises(MarketDataError):
        p.get_quote("AAPL")


def test_fundamentals_and_news_are_not_supported():
    from catalystiq.providers.market_data import MarketDataError

    p = _provider(_FakeMarketData())
    with pytest.raises(MarketDataError):
        p.get_fundamentals("AAPL")
    with pytest.raises(MarketDataError):
        p.get_news("AAPL")


def test_missing_sdk_methods_raise_cleanly():
    from catalystiq.providers.market_data import MarketDataError

    class _Empty:
        pass

    p = WebullMarketDataProvider("k", "s", "us", client=_Empty())
    with pytest.raises(MarketDataError):
        p.get_intraday_ohlcv("X")


def test_requires_credentials_without_injected_client():
    from catalystiq.providers.market_data import MarketDataError

    with pytest.raises(MarketDataError):
        WebullMarketDataProvider("", "", "us")


# --- dedicated intraday-provider factory ------------------------------------


def test_intraday_factory_defaults_to_daily_provider(monkeypatch):
    import catalystiq.providers.market_data as m
    from catalystiq.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("INTRADAY_MARKET_DATA_PROVIDER", "yahoo")
    m.reset_intraday_provider_cache()
    # Default / "yahoo" reuses the daily provider factory (stubbed to avoid
    # constructing the real yfinance-backed provider offline).
    sentinel = object()
    monkeypatch.setattr(m, "get_market_data_provider", lambda: sentinel)
    assert m.get_intraday_market_data_provider() is sentinel
    get_settings.cache_clear()


def test_intraday_factory_returns_and_caches_webull(monkeypatch):
    import catalystiq.providers.market_data as m
    from catalystiq.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("INTRADAY_MARKET_DATA_PROVIDER", "webull")
    monkeypatch.setenv("WEBULL_APP_KEY", "k")
    monkeypatch.setenv("WEBULL_APP_SECRET", "s")
    m.reset_intraday_provider_cache()

    # Avoid constructing the real SDK client: stub the provider class.
    built = []

    class _StubWebull:
        PROVIDER_NAME = "webull_mdata"

        def __init__(self, *a, **k):
            built.append((a, k))

    monkeypatch.setattr(
        "catalystiq.providers.webull_market_data.WebullMarketDataProvider", _StubWebull
    )
    p1 = m.get_intraday_market_data_provider()
    p2 = m.get_intraday_market_data_provider()
    assert isinstance(p1, _StubWebull)
    assert p1 is p2  # cached, built once
    assert len(built) == 1
    m.reset_intraday_provider_cache()
    get_settings.cache_clear()


# --- short-TTL Entry Check cache -------------------------------------------


def test_entry_quality_cache_coalesces_within_ttl():
    import catalystiq.analysis.entry_quality as eq

    calls = {"n": 0}

    class _CountingProvider:
        PROVIDER_NAME = "counting"

        def get_intraday_ohlcv(self, symbol, *, interval="5m", days=20):
            calls["n"] += 1
            return []  # -> insufficient, but still a real computed result to cache

    eq.clear_entry_quality_cache()
    now = dt.datetime(2026, 7, 20, 16, 0, tzinfo=dt.timezone.utc)
    t = [1000.0]
    clock = lambda: t[0]
    p = _CountingProvider()
    eq.score_entry_quality_cached("AAPL", p, now, ttl_seconds=10, monotonic=clock)
    eq.score_entry_quality_cached("AAPL", p, now, ttl_seconds=10, monotonic=clock)
    assert calls["n"] == 1  # second call served from cache
    t[0] += 11  # past TTL
    eq.score_entry_quality_cached("AAPL", p, now, ttl_seconds=10, monotonic=clock)
    assert calls["n"] == 2
    eq.clear_entry_quality_cache()
