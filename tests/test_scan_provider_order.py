"""The opportunity-scan price chain: a configurable, ordered Webull -> Twelve
Data source priority for OHLCV/quotes, independent of the global provider (which
still serves fundamentals/news). Covers primary success, failover on primary
failure, both-fail -> honest unavailable, missing-credential degradation, and
that the request path + background warmer share the same chain. All offline -
fake providers, no network, no SDK.

See catalystiq/providers/market_data.get_scan_market_data_provider and
catalystiq/providers/fallback_market_data.ChainedMarketDataProvider.
"""
from __future__ import annotations

import datetime as dt

import pytest

import catalystiq.providers.market_data as m
from catalystiq.providers.fallback_market_data import ChainedMarketDataProvider
from catalystiq.providers.fundamentals_cache import is_rate_limited_error
from catalystiq.providers.market_data import (
    MarketDataError,
    _UnavailableMarketDataProvider,
    get_scan_market_data_provider,
)
from catalystiq.schemas.market_data import OHLCVBar, Quote

START = dt.date(2026, 1, 1)


def _bar(day: int, close: float) -> OHLCVBar:
    return OHLCVBar(date=dt.date(2026, 1, day), open=close, high=close, low=close,
                    close=close, volume=1000)


class _FakeProvider:
    """A price provider whose calls succeed with a tagged bar/quote, or raise a
    configured error. Records call counts so tests can prove a leg was (not)
    reached."""

    def __init__(self, name: str, *, error: Exception | None = None, close: float = 10.0):
        self.PROVIDER_NAME = name
        self._error = error
        self._close = close
        self.calls: list[str] = []

    def get_ohlcv(self, symbol, start=START, end=None, interval="1d"):
        self.calls.append("get_ohlcv")
        if self._error is not None:
            raise self._error
        return [_bar(2, self._close)]

    def get_quote(self, symbol):
        self.calls.append("get_quote")
        if self._error is not None:
            raise self._error
        return Quote(symbol=symbol.upper(), price=self._close,
                     as_of=dt.datetime.now(dt.timezone.utc))


# --- ChainedMarketDataProvider ---------------------------------------------


def test_primary_success_never_calls_fallback():
    primary = _FakeProvider("webull_mdata", close=11.0)
    fallback = _FakeProvider("twelve_data", close=99.0)
    chain = ChainedMarketDataProvider([primary, fallback])

    bars = chain.get_ohlcv("AAPL", START)
    assert bars[0].close == 11.0  # primary served it
    assert primary.calls == ["get_ohlcv"]
    assert fallback.calls == []  # fallback untouched on primary success


def test_webull_failure_fails_over_to_twelve_data():
    # Primary fails for a NON-rate-limit reason (e.g. entitlement/SDK) - the
    # chain still fails over, because the fallback should cover any failure.
    primary = _FakeProvider("webull_mdata", error=MarketDataError("no market-data entitlement"))
    fallback = _FakeProvider("twelve_data", close=42.0)
    chain = ChainedMarketDataProvider([primary, fallback])

    bars = chain.get_ohlcv("AAPL", START)
    assert bars[0].close == 42.0  # served by Twelve Data
    assert primary.calls == ["get_ohlcv"]
    assert fallback.calls == ["get_ohlcv"]


def test_rate_limited_primary_fails_over_and_quote_too():
    primary = _FakeProvider("webull_mdata", error=MarketDataError("429 Too Many Requests"))
    fallback = _FakeProvider("twelve_data", close=7.0)
    chain = ChainedMarketDataProvider([primary, fallback])

    assert chain.get_quote("MSFT").price == 7.0
    assert fallback.calls == ["get_quote"]


def test_both_providers_failing_raises_marketdataerror_preserving_rate_limit():
    primary = _FakeProvider("webull_mdata", error=MarketDataError("429 Too Many Requests"))
    fallback = _FakeProvider("twelve_data", error=MarketDataError("upstream 500"))
    chain = ChainedMarketDataProvider([primary, fallback])

    with pytest.raises(MarketDataError) as exc:
        chain.get_ohlcv("AAPL", START)
    # Aggregated error names both legs and stays detectable as a rate limit so
    # the scan reports the correct honest "unavailable" reason.
    text = str(exc.value)
    assert "webull_mdata" in text and "twelve_data" in text
    assert is_rate_limited_error(exc.value)


def test_both_failing_non_rate_limit_is_not_marked_rate_limited():
    primary = _FakeProvider("webull_mdata", error=MarketDataError("no entitlement"))
    fallback = _FakeProvider("twelve_data", error=MarketDataError("bad symbol"))
    chain = ChainedMarketDataProvider([primary, fallback])
    with pytest.raises(MarketDataError) as exc:
        chain.get_quote("ZZZZ")
    assert not is_rate_limited_error(exc.value)


def test_chain_does_not_serve_fundamentals_or_news():
    chain = ChainedMarketDataProvider([_FakeProvider("webull_mdata")])
    with pytest.raises(MarketDataError):
        chain.get_fundamentals("AAPL")
    with pytest.raises(MarketDataError):
        chain.get_news("AAPL")


def test_chain_requires_at_least_one_provider():
    with pytest.raises(ValueError):
        ChainedMarketDataProvider([])


# --- get_scan_market_data_provider factory ----------------------------------


def _settings(primary: str, fallback: str):
    class _S:
        market_data_primary_provider = primary
        market_data_fallback_provider = fallback
    return _S()


def test_factory_builds_webull_then_twelve_data_chain(monkeypatch):
    monkeypatch.setattr("catalystiq.config.get_settings", lambda: _settings("webull", "twelve_data"))
    webull = _FakeProvider("webull_mdata")
    twelve = _FakeProvider("twelve_data")
    built = {"webull": webull, "twelve_data": twelve}
    monkeypatch.setattr(m, "_build_named_market_data_provider", lambda name: built[name])

    provider = get_scan_market_data_provider()
    assert isinstance(provider, ChainedMarketDataProvider)
    assert [p.PROVIDER_NAME for p in provider._providers] == ["webull_mdata", "twelve_data"]


def test_factory_skips_unbuildable_primary_uses_fallback_alone(monkeypatch):
    # Missing Webull credentials: the primary can't be built, so the chain is
    # just Twelve Data (returned bare, not wrapped).
    monkeypatch.setattr("catalystiq.config.get_settings", lambda: _settings("webull", "twelve_data"))
    twelve = _FakeProvider("twelve_data")

    def _build(name):
        if name == "webull":
            raise MarketDataError("Webull market-data app_key/app_secret are not configured.")
        return twelve

    monkeypatch.setattr(m, "_build_named_market_data_provider", _build)
    provider = get_scan_market_data_provider()
    assert provider is twelve


def test_factory_degrades_to_unavailable_when_nothing_builds(monkeypatch):
    # Neither Webull creds nor a Twelve Data key: honest "unavailable", not a
    # crash and not a silent Yahoo fallback.
    monkeypatch.setattr("catalystiq.config.get_settings", lambda: _settings("webull", "twelve_data"))

    def _build(name):
        raise MarketDataError(f"{name} not configured")

    monkeypatch.setattr(m, "_build_named_market_data_provider", _build)
    provider = get_scan_market_data_provider()
    assert isinstance(provider, _UnavailableMarketDataProvider)
    with pytest.raises(MarketDataError):
        provider.get_ohlcv("AAPL", START)
    with pytest.raises(MarketDataError):
        provider.get_quote("AAPL")


# --- cold-start scanning through the chain ----------------------------------


def test_cold_start_background_scan_uses_chain_and_fills_cache(monkeypatch):
    """On a cold cache the background compute runs the scan through the SAME
    scan price chain (get_scan_market_data_provider), then caches the result -
    so the Trade Center fills in without ever touching the global provider."""
    import catalystiq.analysis.opportunity_score as ops
    import catalystiq.db.base as dbbase
    import catalystiq.providers.market_data as md
    from catalystiq.analysis.opportunity_score import (
        _SCAN_CACHE,
        _SCAN_INFLIGHT,
        clear_scan_cache,
    )
    from catalystiq.schemas.opportunity import OpportunityScan

    clear_scan_cache()
    _SCAN_INFLIGHT.clear()

    chain = _FakeProvider("webull_mdata>twelve_data_chain")
    monkeypatch.setattr(md, "get_scan_market_data_provider", lambda: chain)

    class _FakeSession:
        def close(self):
            pass

    monkeypatch.setattr(dbbase, "SessionLocal", lambda: _FakeSession())

    used = {}
    now = dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc)

    def _fake_scan_universe(provider, db, when, top=4, universe=None):
        used["provider"] = provider
        return OpportunityScan(
            as_of=now, formula_version="test", universe_size=1, eligible_count=0,
            top=top, candidates=[], ml=ops._ML_NOT_AVAILABLE, note="ok", status="ok",
        )

    monkeypatch.setattr(ops, "scan_universe", _fake_scan_universe)

    key = (ops.SCAN_UNIVERSE, 4)
    _SCAN_INFLIGHT.add(key)
    ops._run_background_scan(4, None, key, monotonic=lambda: 123.0)

    assert used["provider"] is chain  # scanned through the Webull->TwelveData chain
    assert key in _SCAN_CACHE and _SCAN_CACHE[key].scan.status == "ok"
    assert key not in _SCAN_INFLIGHT  # in-flight marker cleared for the next retry
    clear_scan_cache()
    _SCAN_INFLIGHT.clear()


def test_factory_dedups_repeated_provider_name(monkeypatch):
    # primary == fallback: only one leg, returned bare.
    monkeypatch.setattr("catalystiq.config.get_settings", lambda: _settings("webull", "webull"))
    calls = {"n": 0}

    def _build(name):
        calls["n"] += 1
        return _FakeProvider("webull_mdata")

    monkeypatch.setattr(m, "_build_named_market_data_provider", _build)
    provider = get_scan_market_data_provider()
    assert isinstance(provider, _FakeProvider)
    assert calls["n"] == 1  # built once, not twice
