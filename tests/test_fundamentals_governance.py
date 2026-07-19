"""Integration tests for fundamentals governance at the API/scan boundary:

  - the opportunity scan resolves sector from governed data and makes ZERO
    per-symbol fundamentals calls (the request-count reduction),
  - the /market-data/fundamentals endpoint is served through the governed
    cache (a repeat call is a cache hit, not a second provider call),
  - a fundamentals rate-limit does NOT prevent a separate quote from loading
    (quote and fundamentals are independent provider calls).
"""
from __future__ import annotations

import datetime as dt

import pytest

from catalystiq.main import app
from catalystiq.providers.fundamentals_cache import reset_fundamentals_caches
from catalystiq.providers.market_data import MarketDataError, get_market_data_provider
from catalystiq.schemas.market_data import FundamentalsSnapshot, Quote


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _bizdays_ending(end, n):
    days: list[dt.date] = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= dt.timedelta(days=1)
    return list(reversed(days))


def _bar(date, close):
    from catalystiq.schemas.market_data import OHLCVBar

    return OHLCVBar(
        date=date, open=close, high=close + 0.5, low=close - 0.5, close=close, volume=2_000_000
    )


def _rising_bars(n=300, base=100.0, step=0.25):
    dates = _bizdays_ending(dt.date.today(), n)
    return [_bar(day, base + i * step) for i, day in enumerate(dates)]


@pytest.fixture(autouse=True)
def _isolate_caches():
    reset_fundamentals_caches()
    yield
    reset_fundamentals_caches()


class _CountingProvider:
    """Fake provider that counts fundamentals fetches and serves rising OHLCV
    for any symbol, so scoring can succeed for governed universe symbols."""

    PROVIDER_NAME = "counting_fake"

    def __init__(self):
        self.fundamentals_calls = 0

    def get_ohlcv(self, symbol, start, end=None, interval="1d"):
        return _rising_bars()

    def get_quote(self, symbol):
        return Quote(symbol=symbol.upper(), price=175.0, previous_close=174.0, as_of=_now())

    def get_fundamentals(self, symbol):
        self.fundamentals_calls += 1
        return FundamentalsSnapshot(symbol=symbol.upper(), sector="Technology", as_of=_now())

    def get_news(self, symbol, limit=10):
        return []


def test_scan_makes_zero_fundamentals_calls(client):
    provider = _CountingProvider()
    app.dependency_overrides[get_market_data_provider] = lambda: provider
    try:
        # Governed universe symbols (Technology -> XLK) - sector without a fetch.
        r = client.get("/analysis/opportunity-scan", params={"top": 4, "symbols": "NVDA,AAPL,MSFT"})
    finally:
        del app.dependency_overrides[get_market_data_provider]

    assert r.status_code == 200
    # The whole point: the scan resolved sector from governed data, so it never
    # touched the (rate-limited) fundamentals endpoint.
    assert provider.fundamentals_calls == 0


def test_default_universe_scan_makes_zero_fundamentals_calls(client):
    provider = _CountingProvider()
    app.dependency_overrides[get_market_data_provider] = lambda: provider
    try:
        r = client.get("/analysis/opportunity-scan", params={"top": 4})
    finally:
        del app.dependency_overrides[get_market_data_provider]

    assert r.status_code == 200
    body = r.json()
    assert body["universe_size"] == 24  # SCAN_UNIVERSE
    # Before this change a 24-symbol scan issued 24 Yahoo `.info` calls; now 0.
    assert provider.fundamentals_calls == 0


def test_fundamentals_endpoint_served_through_cache(client):
    provider = _CountingProvider()
    app.dependency_overrides[get_market_data_provider] = lambda: provider
    try:
        r1 = client.get("/market-data/fundamentals/NVDA")
        r2 = client.get("/market-data/fundamentals/NVDA")
    finally:
        del app.dependency_overrides[get_market_data_provider]

    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["symbol"] == "NVDA"
    # Second request within TTL is a cache hit - only one provider call total.
    assert provider.fundamentals_calls == 1


class _QuoteOkFundamentalsRateLimited:
    """Quote works; fundamentals is rate-limited - the coupling regression."""

    PROVIDER_NAME = "split_fake"

    def get_ohlcv(self, symbol, start, end=None, interval="1d"):
        return _rising_bars()

    def get_quote(self, symbol):
        return Quote(symbol=symbol.upper(), price=123.45, previous_close=120.0, as_of=_now())

    def get_fundamentals(self, symbol):
        raise MarketDataError(
            f"Failed to fetch fundamentals for {symbol.upper()}: Too Many Requests. Rate limited"
        )

    def get_news(self, symbol, limit=10):
        return []


def test_quote_loads_even_when_fundamentals_is_rate_limited(client):
    provider = _QuoteOkFundamentalsRateLimited()
    app.dependency_overrides[get_market_data_provider] = lambda: provider
    try:
        r_quote = client.get("/market-data/quote/NVDA")
        r_fund = client.get("/market-data/fundamentals/NVDA")
        # A quote fetched AFTER the fundamentals failure still works: the two
        # are independent provider calls on independent routes.
        r_quote_again = client.get("/market-data/quote/NVDA")
    finally:
        del app.dependency_overrides[get_market_data_provider]

    assert r_quote.status_code == 200
    assert r_quote.json()["price"] == 123.45
    assert r_quote.json()["as_of"] is not None
    assert r_fund.status_code == 502  # fundamentals rate-limited -> 502
    assert r_quote_again.status_code == 200
    assert r_quote_again.json()["price"] == 123.45
