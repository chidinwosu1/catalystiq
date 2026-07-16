"""YahooFinanceProvider parsing/mapping tests. The `yfinance.Ticker` object is
mocked out so these run without network access - the sandbox this was built
in has Yahoo Finance's hosts blocked by egress policy, and even without that
constraint these should stay deterministic and offline.
"""
import datetime as dt
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from catalystiq.providers.market_data import MarketDataError, YahooFinanceProvider


@pytest.fixture
def provider():
    return YahooFinanceProvider()


def test_get_quote_maps_fields(provider, monkeypatch):
    fake_ticker = MagicMock()
    fake_ticker.fast_info = {"last_price": 123.45, "previous_close": 120.0}
    monkeypatch.setattr(provider, "_ticker", lambda symbol: fake_ticker)

    quote = provider.get_quote("aapl")

    assert quote.symbol == "AAPL"
    assert quote.price == 123.45
    assert quote.previous_close == 120.0


def test_get_quote_raises_market_data_error_when_price_missing(provider, monkeypatch):
    fake_ticker = MagicMock()
    fake_ticker.fast_info = {"last_price": None}
    monkeypatch.setattr(provider, "_ticker", lambda symbol: fake_ticker)

    with pytest.raises(MarketDataError):
        provider.get_quote("aapl")


def test_get_ohlcv_maps_dataframe_rows(provider, monkeypatch):
    index = pd.to_datetime(["2024-01-02", "2024-01-03"])
    df = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [101.0, 102.0],
            "Volume": [1_000_000, 1_100_000],
        },
        index=index,
    )
    fake_ticker = MagicMock()
    fake_ticker.history.return_value = df
    monkeypatch.setattr(provider, "_ticker", lambda symbol: fake_ticker)

    bars = provider.get_ohlcv("aapl", start=dt.date(2024, 1, 2), end=dt.date(2024, 1, 3))

    assert len(bars) == 2
    assert bars[0].date == dt.date(2024, 1, 2)
    assert bars[0].close == 101.0
    assert bars[1].volume == 1_100_000


def test_get_ohlcv_empty_dataframe_returns_empty_list(provider, monkeypatch):
    fake_ticker = MagicMock()
    fake_ticker.history.return_value = pd.DataFrame()
    monkeypatch.setattr(provider, "_ticker", lambda symbol: fake_ticker)

    assert provider.get_ohlcv("aapl", start=dt.date(2024, 1, 2)) == []


def test_get_ohlcv_wraps_exceptions(provider, monkeypatch):
    fake_ticker = MagicMock()
    fake_ticker.history.side_effect = RuntimeError("boom")
    monkeypatch.setattr(provider, "_ticker", lambda symbol: fake_ticker)

    with pytest.raises(MarketDataError):
        provider.get_ohlcv("aapl", start=dt.date(2024, 1, 2))


def test_get_fundamentals_maps_known_fields(provider, monkeypatch):
    fake_ticker = MagicMock()
    fake_ticker.info = {
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "marketCap": 3_000_000_000_000,
        "trailingPE": 30.5,
        "returnOnEquity": 1.5,
    }
    monkeypatch.setattr(provider, "_ticker", lambda symbol: fake_ticker)

    snap = provider.get_fundamentals("aapl")

    assert snap.symbol == "AAPL"
    assert snap.sector == "Technology"
    assert snap.trailing_pe == 30.5
    assert snap.return_on_equity == 1.5
    assert snap.forward_pe is None


def test_get_news_maps_content_items(provider, monkeypatch):
    fake_ticker = MagicMock()
    fake_ticker.get_news.return_value = [
        {
            "content": {
                "title": "Company beats earnings",
                "canonicalUrl": {"url": "https://example.com/story"},
                "pubDate": "2024-01-02T13:00:00Z",
                "contentType": "STORY",
                "summary": "Summary text",
            }
        }
    ]
    monkeypatch.setattr(provider, "_ticker", lambda symbol: fake_ticker)

    items = provider.get_news("aapl", limit=5)

    assert len(items) == 1
    assert items[0].headline == "Company beats earnings"
    assert items[0].source_url == "https://example.com/story"
    assert items[0].published_at == dt.datetime(2024, 1, 2, 13, 0, tzinfo=dt.timezone.utc)
