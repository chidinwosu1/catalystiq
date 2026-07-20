"""The opportunity scan's short-TTL result cache: within the TTL a repeat scan
reuses the computed result (no re-run); after the TTL it recomputes; ttl=0
disables it."""
from __future__ import annotations

import datetime as dt

import catalystiq.analysis.opportunity_score as osmod
from catalystiq.analysis.opportunity_score import clear_scan_cache, scan_universe_cached
from catalystiq.providers.market_data import MarketDataError
from catalystiq.schemas.market_data import Quote


class _Clock:
    def __init__(self, start=1000.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class _FastProvider:
    """Returns empty history so scoring is quick (symbols end up skipped); the
    tests care about how often the scan LOOP runs, not the scores."""

    def get_ohlcv(self, symbol, start, end=None, interval="1d"):
        return []

    def get_quote(self, symbol):
        return Quote(symbol=symbol.upper(), price=1.0, previous_close=1.0,
                     as_of=dt.datetime.now(dt.timezone.utc))

    def get_fundamentals(self, symbol):
        raise MarketDataError("n/a")

    def get_news(self, symbol, limit=10):
        return []


def _spy_on_scan(monkeypatch):
    calls = {"n": 0}
    original = osmod.scan_universe

    def spy(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(osmod, "scan_universe", spy)
    return calls


def test_scan_cache_hits_within_ttl(test_db_session, monkeypatch):
    clear_scan_cache()
    calls = _spy_on_scan(monkeypatch)
    clock = _Clock()
    provider = _FastProvider()
    now = dt.datetime.now(dt.timezone.utc)

    s1 = scan_universe_cached(
        provider, test_db_session, now, top=4, universe=["NVDA", "AAPL"],
        ttl_seconds=60, monotonic=clock,
    )
    s2 = scan_universe_cached(
        provider, test_db_session, now, top=4, universe=["NVDA", "AAPL"],
        ttl_seconds=60, monotonic=clock,
    )
    assert calls["n"] == 1  # second served from cache - loop ran once
    assert s2 is s1

    clock.advance(61)  # TTL expired
    s3 = scan_universe_cached(
        provider, test_db_session, now, top=4, universe=["NVDA", "AAPL"],
        ttl_seconds=60, monotonic=clock,
    )
    assert calls["n"] == 2
    assert s3 is not s1
    clear_scan_cache()


def test_scan_cache_keyed_by_universe_and_top(test_db_session, monkeypatch):
    clear_scan_cache()
    calls = _spy_on_scan(monkeypatch)
    clock = _Clock()
    provider = _FastProvider()
    now = dt.datetime.now(dt.timezone.utc)

    scan_universe_cached(provider, test_db_session, now, top=4, universe=["NVDA"],
                         ttl_seconds=60, monotonic=clock)
    scan_universe_cached(provider, test_db_session, now, top=4, universe=["AAPL"],
                         ttl_seconds=60, monotonic=clock)  # different universe
    scan_universe_cached(provider, test_db_session, now, top=2, universe=["NVDA"],
                         ttl_seconds=60, monotonic=clock)  # different top
    assert calls["n"] == 3  # each distinct key computed
    clear_scan_cache()


def test_scan_cache_disabled_when_ttl_zero(test_db_session, monkeypatch):
    clear_scan_cache()
    calls = _spy_on_scan(monkeypatch)
    provider = _FastProvider()
    now = dt.datetime.now(dt.timezone.utc)

    scan_universe_cached(provider, test_db_session, now, universe=["NVDA"], ttl_seconds=0)
    scan_universe_cached(provider, test_db_session, now, universe=["NVDA"], ttl_seconds=0)
    assert calls["n"] == 2  # no caching
    clear_scan_cache()
