"""FallbackMarketDataProvider: fail over to the secondary ONLY on an upstream
rate limit, for price/quote calls only; fundamentals/news stay on the primary.
Plus the get_market_data_provider() wiring (opt-in, off by default)."""
from __future__ import annotations

import datetime as dt

import pytest

from catalystiq.providers.fallback_market_data import FallbackMarketDataProvider
from catalystiq.providers.market_data import MarketDataError
from catalystiq.schemas.market_data import IntradayBar, OHLCVBar, Quote


def _quote(sym, price):
    return Quote(symbol=sym.upper(), price=price, as_of=dt.datetime.now(dt.timezone.utc))


class _Primary:
    PROVIDER_NAME = "primary"

    def __init__(self, *, error=None):
        self._error = error
        self.fund_calls = 0

    def get_quote(self, symbol):
        if self._error:
            raise self._error
        return _quote(symbol, 100.0)

    def get_ohlcv(self, symbol, start, end=None, interval="1d"):
        if self._error:
            raise self._error
        return [OHLCVBar(date=start, open=1, high=1, low=1, close=1, volume=1)]

    def get_intraday_ohlcv(self, symbol, *, interval="5m", days=20):
        if self._error:
            raise self._error
        return [IntradayBar(timestamp=dt.datetime(2026, 7, 20, tzinfo=dt.timezone.utc),
                            open=1, high=1, low=1, close=1, volume=1)]

    def get_fundamentals(self, symbol):
        self.fund_calls += 1
        return {"symbol": symbol}

    def get_news(self, symbol, limit=10):
        return ["primary-news"]


class _Secondary:
    PROVIDER_NAME = "secondary"

    def __init__(self):
        self.calls = []

    def get_quote(self, symbol):
        self.calls.append("get_quote")
        return _quote(symbol, 200.0)

    def get_ohlcv(self, symbol, start, end=None, interval="1d"):
        self.calls.append("get_ohlcv")
        return [OHLCVBar(date=start, open=2, high=2, low=2, close=2, volume=2)]

    def get_intraday_ohlcv(self, symbol, *, interval="5m", days=20):
        self.calls.append("get_intraday_ohlcv")
        return [IntradayBar(timestamp=dt.datetime(2026, 7, 20, tzinfo=dt.timezone.utc),
                            open=2, high=2, low=2, close=2, volume=2)]


_RATE_LIMIT = MarketDataError("Too Many Requests. Rate limited")


def test_primary_success_never_touches_secondary():
    sec = _Secondary()
    p = FallbackMarketDataProvider(_Primary(), sec)
    assert p.get_quote("AAPL").price == 100.0
    assert p.get_ohlcv("AAPL", dt.date(2026, 7, 20))[0].close == 1
    assert sec.calls == []


def test_rate_limit_fails_over_to_secondary():
    sec = _Secondary()
    p = FallbackMarketDataProvider(_Primary(error=_RATE_LIMIT), sec)
    assert p.get_quote("AAPL").price == 200.0  # secondary served it
    assert p.get_ohlcv("AAPL", dt.date(2026, 7, 20))[0].close == 2
    assert p.get_intraday_ohlcv("AAPL")[0].close == 2
    assert sec.calls == ["get_quote", "get_ohlcv", "get_intraday_ohlcv"]


def test_non_rate_limit_error_is_not_masked():
    sec = _Secondary()
    boom = MarketDataError("No data for BADSYM")  # not a throttle
    p = FallbackMarketDataProvider(_Primary(error=boom), sec)
    with pytest.raises(MarketDataError):
        p.get_quote("BADSYM")
    assert sec.calls == []  # secondary never consulted for a non-throttle error


def test_fundamentals_and_news_stay_on_primary():
    sec = _Secondary()
    primary = _Primary()
    p = FallbackMarketDataProvider(primary, sec)
    assert p.get_fundamentals("AAPL") == {"symbol": "AAPL"}
    assert p.get_news("AAPL") == ["primary-news"]
    assert primary.fund_calls == 1
    assert sec.calls == []


def test_provider_name_is_composite():
    p = FallbackMarketDataProvider(_Primary(), _Secondary())
    assert p.PROVIDER_NAME == "primary+secondary_fallback"


# --- get_market_data_provider() wiring --------------------------------------


def test_factory_no_fallback_by_default(monkeypatch):
    import catalystiq.providers.market_data as m
    from catalystiq.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("MARKET_DATA_FALLBACK_PROVIDER", "")
    # Avoid building the real yfinance provider offline.
    monkeypatch.setattr(m, "YahooFinanceProvider", lambda: _Primary())
    provider = m.get_market_data_provider()
    assert isinstance(provider, _Primary)
    get_settings.cache_clear()


def test_factory_wraps_with_fallback_when_configured(monkeypatch):
    import catalystiq.providers.market_data as m
    from catalystiq.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("MARKET_DATA_FALLBACK_PROVIDER", "webull")
    monkeypatch.setattr(m, "YahooFinanceProvider", lambda: _Primary())
    monkeypatch.setattr(m, "get_webull_market_data_provider", lambda: _Secondary())
    provider = m.get_market_data_provider()
    assert isinstance(provider, FallbackMarketDataProvider)
    get_settings.cache_clear()


def test_factory_skips_fallback_when_secondary_unavailable(monkeypatch):
    import catalystiq.providers.market_data as m
    from catalystiq.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("MARKET_DATA_FALLBACK_PROVIDER", "webull")
    monkeypatch.setattr(m, "YahooFinanceProvider", lambda: _Primary())

    def _no_creds():
        raise MarketDataError("Webull market-data app_key/app_secret are not configured.")

    monkeypatch.setattr(m, "get_webull_market_data_provider", _no_creds)
    provider = m.get_market_data_provider()
    # Degrades to the bare primary rather than raising.
    assert isinstance(provider, _Primary)
    get_settings.cache_clear()
