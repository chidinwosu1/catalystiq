"""Twelve Data adapter (offline) + cross-provider comparison: values recorded
and differenced, never averaged; configured-only fallback; request budget."""
import datetime as dt

import pytest

from catalystiq.db import models
from catalystiq.pipelines import comparison as cmp
from catalystiq.providers.base import ProviderError, ProviderErrorCategory
from catalystiq.providers.market_data import MarketDataError, MarketDataProvider
from catalystiq.providers.transport import HttpResponse
from catalystiq.providers.twelve_data import TwelveDataProvider
from catalystiq.schemas.market_data import OHLCVBar, Quote


class FakeTransport:
    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def request(self, method, url, *, params=None, headers=None, json=None):
        self.requests.append({"url": url, "params": params})
        for key, (status, text) in self.routes.items():
            if key in url:
                return HttpResponse(status, {}, text, url, 1.0, 0, "twelve_data")
        return HttpResponse(404, {}, "{}", url, 1.0, 0, "twelve_data")


_QUOTE = '{"symbol":"AAPL","close":"195.20","previous_close":"194.00","timestamp":1752710400}'
_TS = """{"status":"ok","values":[
 {"datetime":"2026-07-16","open":"193.0","high":"196.0","low":"192.5","close":"195.2","volume":"50000000"},
 {"datetime":"2026-07-15","open":"191.0","high":"194.0","low":"190.0","close":"193.5","volume":"48000000"}
]}"""
_ERR = '{"status":"error","message":"invalid symbol","code":400}'


def _provider(routes, budget=0):
    return TwelveDataProvider("k", transport=FakeTransport(routes), request_budget=budget)


def test_requires_key():
    with pytest.raises(ProviderError) as exc:
        TwelveDataProvider("")
    assert exc.value.category is ProviderErrorCategory.CONFIG


def test_quote_and_ohlcv_parse():
    provider = _provider({"quote": (200, _QUOTE), "time_series": (200, _TS)})
    q = provider.get_quote("AAPL")
    assert q.price == 195.20
    assert q.previous_close == 194.00
    bars = provider.get_ohlcv("AAPL", start=dt.date(2026, 7, 15), interval="1d")
    # Sorted ascending.
    assert [b.date for b in bars] == [dt.date(2026, 7, 15), dt.date(2026, 7, 16)]


def test_error_body_raises():
    provider = _provider({"time_series": (200, _ERR)})
    with pytest.raises(ProviderError) as exc:
        provider.get_ohlcv("BAD", start=dt.date(2026, 7, 1))
    assert exc.value.category is ProviderErrorCategory.UNAVAILABLE


def test_request_budget_enforced():
    provider = _provider({"quote": (200, _QUOTE)}, budget=2)
    provider.get_quote("AAPL")
    provider.get_quote("AAPL")
    with pytest.raises(ProviderError) as exc:
        provider.get_quote("AAPL")
    assert exc.value.category is ProviderErrorCategory.RATE_LIMITED


def test_fundamentals_and_news_not_supported():
    provider = _provider({"quote": (200, _QUOTE)})
    with pytest.raises(MarketDataError):
        provider.get_fundamentals("AAPL")
    with pytest.raises(MarketDataError):
        provider.get_news("AAPL")


# --- comparison ---------------------------------------------------------


class StubProvider(MarketDataProvider):
    PROVIDER_NAME = "stub"

    def __init__(self, name, price):
        self.PROVIDER_NAME = name
        self._price = price

    def get_quote(self, symbol):
        if self._price is None:
            raise MarketDataError("down")
        return Quote(symbol=symbol, price=self._price, as_of=dt.datetime.now(dt.timezone.utc))

    def get_ohlcv(self, symbol, start, end=None, interval="1d"):
        if self._price is None:
            raise MarketDataError("down")
        return [OHLCVBar(date=start, open=1, high=1, low=1, close=self._price, volume=1)]

    def get_fundamentals(self, symbol):
        raise MarketDataError("n/a")

    def get_news(self, symbol, limit=10):
        raise MarketDataError("n/a")


def test_comparison_records_both_values_never_averages(test_db_session):
    db = test_db_session
    primary = StubProvider("yahoo", 100.0)
    secondary = StubProvider("twelve_data", 100.4)
    row = cmp.compare_quotes("AAPL", db, primary, secondary, tolerance_pct=0.5)
    assert row.primary_value == 100.0
    assert row.secondary_value == 100.4
    # Difference recorded, not averaged; selected = primary (Yahoo priority).
    assert row.relative_diff_pct == pytest.approx(0.4, abs=1e-6)
    assert row.within_tolerance is True
    assert row.selected_provider == "yahoo"
    # Nothing stored is the average of the two.
    assert row.primary_value != pytest.approx(100.2)


def test_comparison_flags_out_of_tolerance(test_db_session):
    db = test_db_session
    row = cmp.compare_quotes(
        "AAPL", db, StubProvider("yahoo", 100.0), StubProvider("twelve_data", 105.0),
        tolerance_pct=0.5,
    )
    assert row.within_tolerance is False
    assert "exceeds tolerance" in row.selected_reason
    summary = cmp.comparison_summary(db)
    assert summary["out_of_tolerance"] == 1
    assert "AAPL" in summary["out_of_tolerance_symbols"]


def test_fallback_only_when_secondary_provided():
    down = StubProvider("yahoo", None)
    up = StubProvider("twelve_data", 50.0)
    # No secondary -> primary failure propagates.
    with pytest.raises(MarketDataError):
        cmp.get_ohlcv_with_fallback("AAPL", dt.date(2026, 7, 1), down, None)
    # With secondary -> falls back, reports which provider was used.
    bars, used = cmp.get_ohlcv_with_fallback("AAPL", dt.date(2026, 7, 1), down, up)
    assert used == "twelve_data"
    assert bars[0].close == 50.0


def test_healthy_primary_never_uses_secondary():
    primary = StubProvider("yahoo", 10.0)
    secondary = StubProvider("twelve_data", 999.0)
    bars, used = cmp.get_ohlcv_with_fallback("AAPL", dt.date(2026, 7, 1), primary, secondary)
    assert used == "yahoo"
    assert bars[0].close == 10.0
