"""Finnhub company-news adapter: article mapping, most-recent-first ordering,
per-symbol TTL caching (rate-budget protection), missing-key handling, and
graceful failure on transport/HTTP errors. Offline - fake transport, no network.
"""
from __future__ import annotations

import datetime as dt

import pytest

from catalystiq.providers.base import ProviderError, ProviderErrorCategory
from catalystiq.providers.finnhub_news import FinnhubNewsProvider
from catalystiq.providers.market_data import MarketDataError


def _epoch(y, mo, d) -> int:
    return int(dt.datetime(y, mo, d, tzinfo=dt.timezone.utc).timestamp())


class _FakeResp:
    def __init__(self, body, *, error: ProviderError | None = None):
        self._body = body
        self._error = error

    def raise_for_status(self):
        if self._error is not None:
            raise self._error
        return self

    def json(self):
        return self._body


class _FakeTransport:
    """Mimics HttpTransport.request: returns canned news JSON (or raises a
    ProviderError to simulate a 401/429/5xx) and counts calls so caching is
    provable."""

    def __init__(self, body, *, error: ProviderError | None = None):
        self._body = body
        self._error = error
        self.calls = 0

    def request(self, method, url, *, params=None, headers=None, json=None):
        self.calls += 1
        self.last_params = params
        return _FakeResp(self._body, error=self._error)


_ARTICLES = [
    {"headline": "Older headline", "url": "https://x/1", "datetime": _epoch(2026, 1, 1),
     "summary": "s1", "source": "Reuters"},
    {"headline": "Newer headline", "url": "https://x/2", "datetime": _epoch(2026, 1, 5),
     "summary": "s2", "source": "Bloomberg"},
]


def _provider(transport, **kw):
    clock = kw.pop("clock", lambda: 1000.0)
    return FinnhubNewsProvider("key", transport=transport, monotonic=clock, **kw)


def test_maps_articles_and_sorts_recent_first():
    t = _FakeTransport(_ARTICLES)
    items = _provider(t).get_news("aapl", limit=10)
    assert [i.headline for i in items] == ["Newer headline", "Older headline"]
    assert items[0].source_url == "https://x/2"
    assert items[0].category == "Bloomberg"
    assert items[0].published_at == dt.datetime(2026, 1, 5, tzinfo=dt.timezone.utc)
    # Symbol upper-cased and a from/to window sent.
    assert t.last_params["symbol"] == "AAPL"
    assert "from" in t.last_params and "to" in t.last_params
    assert t.last_params["token"] == "key"


def test_limit_slices_after_sort():
    items = _provider(_FakeTransport(_ARTICLES)).get_news("AAPL", limit=1)
    assert len(items) == 1 and items[0].headline == "Newer headline"


def test_skips_incomplete_articles_never_fabricates():
    body = [
        {"headline": "", "url": "https://x/1", "datetime": _epoch(2026, 1, 1)},  # no headline
        {"headline": "No url", "url": "", "datetime": _epoch(2026, 1, 2)},        # no url
        {"headline": "Good", "url": "https://x/3", "datetime": _epoch(2026, 1, 3)},
    ]
    items = _provider(_FakeTransport(body)).get_news("AAPL")
    assert [i.headline for i in items] == ["Good"]


def test_cache_coalesces_within_ttl_then_refetches():
    t = _FakeTransport(_ARTICLES)
    clock = [1000.0]
    p = _provider(t, clock=lambda: clock[0], cache_ttl_seconds=600)
    p.get_news("AAPL", limit=10)
    p.get_news("AAPL", limit=5)  # served from cache (different limit, same fetch)
    assert t.calls == 1
    clock[0] += 601  # past TTL
    p.get_news("AAPL", limit=10)
    assert t.calls == 2


def test_rate_limit_error_becomes_marketdataerror():
    err = ProviderError("HTTP 429", category=ProviderErrorCategory.RATE_LIMITED,
                        provider="finnhub")
    t = _FakeTransport(None, error=err)
    with pytest.raises(MarketDataError) as exc:
        _provider(t).get_news("AAPL")
    assert "429" in str(exc.value)


def test_empty_window_returns_empty_list():
    assert _provider(_FakeTransport([])).get_news("AAPL") == []


def test_missing_api_key_raises():
    with pytest.raises(ProviderError):
        FinnhubNewsProvider("")


def test_factory_missing_key_raises_marketdataerror(monkeypatch):
    from catalystiq.providers import finnhub_news as fn

    class _S:
        finnhub_api_key = ""
        finnhub_news_lookback_days = 14
        finnhub_news_cache_ttl_seconds = 600

    monkeypatch.setattr("catalystiq.config.get_settings", lambda: _S())
    with pytest.raises(MarketDataError):
        fn.get_finnhub_news_provider()
