"""Market-data diagnostics endpoint: live provider probes, rate-limit
classification, gate/scan-cache reporting, and the summary diagnosis. Offline -
fake providers, no network."""
from __future__ import annotations

import datetime as dt

import pytest

from catalystiq.providers.market_data import MarketDataError
from catalystiq.schemas.market_data import IntradayBar, Quote


class _HealthyProvider:
    PROVIDER_NAME = "fake_healthy"

    def get_quote(self, symbol):
        return Quote(symbol=symbol.upper(), price=100.0, as_of=dt.datetime.now(dt.timezone.utc))

    def get_intraday_ohlcv(self, symbol, *, interval="5m", days=20):
        return [
            IntradayBar(
                timestamp=dt.datetime(2026, 7, 20, 13, 30, tzinfo=dt.timezone.utc),
                open=1, high=1, low=1, close=1, volume=1,
            )
        ]


class _RateLimitedProvider:
    PROVIDER_NAME = "fake_ratelimited"

    def get_quote(self, symbol):
        raise MarketDataError("Too Many Requests. Rate limited")

    def get_intraday_ohlcv(self, symbol, *, interval="5m", days=20):
        raise MarketDataError("Too Many Requests. Rate limited")


@pytest.fixture(autouse=True)
def _reset_gates():
    from catalystiq.providers.market_data_gate import reset_market_data_gates

    reset_market_data_gates()
    yield
    reset_market_data_gates()


def _probe(client, app):
    return client.get("/analysis/diagnostics/market-data")


def test_diagnostics_healthy(client, monkeypatch):
    import catalystiq.providers.market_data as m
    from catalystiq.main import app

    monkeypatch.setattr(m, "get_market_data_provider", lambda: _HealthyProvider())
    monkeypatch.setattr(m, "get_intraday_market_data_provider", lambda: _HealthyProvider())

    r = client.get("/analysis/diagnostics/market-data")
    assert r.status_code == 200
    body = r.json()
    assert body["daily_provider_probe"]["ok"] is True
    assert body["daily_provider_probe"]["rate_limited"] is False
    assert body["intraday_provider_probe"]["ok"] is True
    assert "quote ok" in body["daily_provider_probe"]["detail"]
    assert "rate limit" not in body["summary"].lower()


def test_diagnostics_detects_rate_limit(client, monkeypatch):
    import catalystiq.providers.market_data as m

    monkeypatch.setattr(m, "get_market_data_provider", lambda: _RateLimitedProvider())
    monkeypatch.setattr(m, "get_intraday_market_data_provider", lambda: _RateLimitedProvider())

    r = client.get("/analysis/diagnostics/market-data")
    assert r.status_code == 200
    body = r.json()
    assert body["daily_provider_probe"]["ok"] is False
    assert body["daily_provider_probe"]["rate_limited"] is True
    # The summary names the upstream throttle as the cause, not Entry Check.
    assert "rate limit" in body["summary"].lower()
    assert "entry check" in body["summary"].lower()


def test_diagnostics_reports_provider_construction_failure(client, monkeypatch):
    import catalystiq.providers.market_data as m

    def _boom():
        raise RuntimeError("yfinance not installed")

    monkeypatch.setattr(m, "get_market_data_provider", _boom)
    monkeypatch.setattr(m, "get_intraday_market_data_provider", lambda: _HealthyProvider())

    r = client.get("/analysis/diagnostics/market-data")
    assert r.status_code == 200
    body = r.json()
    assert body["daily_provider_probe"]["ok"] is False
    assert body["daily_provider_probe"]["provider"] == "unavailable"
    assert "yfinance" in body["daily_provider_probe"]["detail"]


def test_diagnostics_reports_config_and_scan_cache(client, monkeypatch):
    import catalystiq.providers.market_data as m

    monkeypatch.setattr(m, "get_market_data_provider", lambda: _HealthyProvider())
    monkeypatch.setattr(m, "get_intraday_market_data_provider", lambda: _HealthyProvider())

    r = client.get("/analysis/diagnostics/market-data")
    body = r.json()
    assert set(body["config"]) == {
        "market_data_provider",
        "intraday_market_data_provider",
        "webull_market_data_configured",
    }
    assert "cached_scans" in body["scan_cache"]
    assert "background_warm_in_flight" in body["scan_cache"]
    assert "gate_stats" in body
