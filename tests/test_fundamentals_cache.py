"""Unit tests for the governed fundamentals cache: TTL, single-flight
de-duplication, per-provider concurrency limit, and rate-limit cooldown.

Everything is driven with an injected clock and deterministic threading
(events / polled counters), so there is no real network and no real waiting.
"""
from __future__ import annotations

import datetime as dt
import threading
import time

import pytest

from catalystiq.providers.fundamentals_cache import (
    FundamentalsCache,
    is_rate_limited_error,
)
from catalystiq.providers.market_data import MarketDataError
from catalystiq.schemas.market_data import FundamentalsSnapshot


def _snap(symbol="AAPL", sector="Technology", as_of=None):
    return FundamentalsSnapshot(
        symbol=symbol.upper(),
        sector=sector,
        as_of=as_of or dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc),
    )


class _Clock:
    def __init__(self, start=1000.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def _cache(clock=None, **kw):
    params = dict(
        ttl_seconds=100.0,
        max_concurrency=4,
        rate_limit_threshold=3,
        cooldown_seconds=60.0,
    )
    params.update(kw)
    if clock is not None:
        params["monotonic"] = clock
    return FundamentalsCache(**params)


def _wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


# --- TTL --------------------------------------------------------------------


def test_cache_hit_avoids_second_provider_call_and_preserves_as_of():
    cache = _cache(_Clock())
    calls = {"n": 0}
    original = _snap(as_of=dt.datetime(2020, 5, 5, tzinfo=dt.timezone.utc))

    def fetch(sym):
        calls["n"] += 1
        return original

    first = cache.get("AAPL", fetch)
    second = cache.get("aapl", fetch)  # different case, same symbol

    assert calls["n"] == 1
    assert cache.stats.provider_calls == 1
    assert cache.stats.cache_hits == 1
    # A cache hit returns the ORIGINAL snapshot with its true retrieval time -
    # the as_of is never re-stamped to "now".
    assert first.as_of == second.as_of == original.as_of


def test_cache_expires_after_ttl():
    clock = _Clock()
    cache = _cache(clock, ttl_seconds=100.0)
    calls = {"n": 0}

    def fetch(sym):
        calls["n"] += 1
        return _snap()

    cache.get("AAPL", fetch)
    clock.advance(50)
    cache.get("AAPL", fetch)  # still fresh -> served from cache
    assert calls["n"] == 1

    clock.advance(60)  # elapsed 110 > ttl 100 -> expired
    cache.get("AAPL", fetch)
    assert calls["n"] == 2
    assert cache.stats.provider_calls == 2


# --- single-flight de-duplication ------------------------------------------


def test_concurrent_identical_calls_coalesce_to_one_fetch():
    cache = _cache(_Clock())
    started = threading.Event()
    release = threading.Event()
    calls = {"n": 0}

    def fetch(sym):
        calls["n"] += 1
        started.set()
        release.wait(5)
        return _snap(sym)

    results: dict[str, FundamentalsSnapshot] = {}

    def worker(name):
        results[name] = cache.get("AAPL", fetch)

    t1 = threading.Thread(target=worker, args=("a",))
    t1.start()
    assert started.wait(5)  # 'a' is now inside fetch, holding the in-flight slot

    t2 = threading.Thread(target=worker, args=("b",))
    t2.start()
    # 'b' should coalesce onto 'a' without a second fetch. Wait for that state
    # deterministically before releasing.
    assert _wait_until(lambda: cache.stats.dedup_coalesced == 1)

    release.set()
    t1.join(5)
    t2.join(5)

    assert calls["n"] == 1
    assert cache.stats.provider_calls == 1
    assert cache.stats.dedup_coalesced == 1
    assert results["a"].symbol == results["b"].symbol == "AAPL"


def test_coalesced_waiter_receives_the_same_error():
    cache = _cache(_Clock())
    started = threading.Event()
    release = threading.Event()

    def fetch(sym):
        started.set()
        release.wait(5)
        raise MarketDataError("boom")

    errors: dict[str, Exception] = {}

    def worker(name):
        try:
            cache.get("AAPL", fetch)
        except Exception as exc:  # noqa: BLE001
            errors[name] = exc

    t1 = threading.Thread(target=worker, args=("a",))
    t1.start()
    assert started.wait(5)
    t2 = threading.Thread(target=worker, args=("b",))
    t2.start()
    assert _wait_until(lambda: cache.stats.dedup_coalesced == 1)
    release.set()
    t1.join(5)
    t2.join(5)

    assert isinstance(errors["a"], MarketDataError)
    assert isinstance(errors["b"], MarketDataError)
    assert str(errors["a"]) == str(errors["b"]) == "boom"


# --- concurrency limit ------------------------------------------------------


def test_concurrency_limit_serializes_distinct_symbols():
    cache = _cache(_Clock(), max_concurrency=1)
    entered = threading.Semaphore(0)
    release = threading.Event()
    lock = threading.Lock()
    live = {"now": 0, "max": 0}

    def fetch(sym):
        with lock:
            live["now"] += 1
            live["max"] = max(live["max"], live["now"])
        entered.release()
        release.wait(5)
        with lock:
            live["now"] -= 1
        return _snap(sym)

    threads = [
        threading.Thread(target=lambda s=s: cache.get(s, fetch))
        for s in ("AAPL", "MSFT", "GOOGL")
    ]
    for t in threads:
        t.start()

    # One fetch is running; give the others a chance to (wrongly) enter too.
    assert entered.acquire(timeout=5)
    time.sleep(0.1)
    release.set()
    for t in threads:
        t.join(5)

    assert live["max"] == 1  # never more than one concurrent provider call


# --- rate-limit cooldown / circuit breaker ---------------------------------


def test_rate_limit_cooldown_opens_then_recovers():
    clock = _Clock()
    cache = _cache(clock, rate_limit_threshold=2, cooldown_seconds=60.0)
    state = {"fail": True}

    def fetch(sym):
        if state["fail"]:
            raise MarketDataError(
                "Failed to fetch fundamentals for NVDA: Too Many Requests. Rate limited"
            )
        return _snap(sym)

    # Two consecutive 429s trip the cooldown.
    for _ in range(2):
        with pytest.raises(MarketDataError):
            cache.get("NVDA", fetch)
    assert cache.stats.provider_calls == 2
    assert cache.stats.rate_limited == 2

    # While cooling down, further calls fail fast WITHOUT hitting the provider.
    with pytest.raises(MarketDataError) as excinfo:
        cache.get("NVDA", fetch)
    assert "cooldown" in str(excinfo.value).lower()
    assert cache.stats.provider_calls == 2  # unchanged - no provider call
    assert cache.stats.cooldown_short_circuits == 1
    assert cache.cooldown_active

    # After the cooldown window a single trial is allowed; it now succeeds.
    clock.advance(61)
    state["fail"] = False
    snap = cache.get("NVDA", fetch)
    assert snap.symbol == "NVDA"
    assert cache.stats.provider_calls == 3
    assert not cache.cooldown_active


def test_non_rate_limit_error_never_opens_cooldown():
    cache = _cache(_Clock(), rate_limit_threshold=1, cooldown_seconds=60.0)

    def fetch(sym):
        raise MarketDataError("SEC EDGAR has no CIK for symbol 'ZZZZ'")

    for _ in range(3):
        with pytest.raises(MarketDataError):
            cache.get("ZZZZ", fetch)

    # A normal (non-throttle) error keeps trying and never trips the breaker.
    assert cache.stats.provider_calls == 3
    assert cache.stats.errors == 3
    assert cache.stats.rate_limited == 0
    assert not cache.cooldown_active


def test_is_rate_limited_error_detection():
    assert is_rate_limited_error(
        MarketDataError("Failed to fetch fundamentals for NVDA: Too Many Requests. Rate limited")
    )
    assert is_rate_limited_error(Exception("upstream returned HTTP 429"))
    assert is_rate_limited_error(RuntimeError("Rate limit exceeded"))
    assert not is_rate_limited_error(MarketDataError("No fundamentals available for XYZ"))


def test_max_concurrency_must_be_positive():
    with pytest.raises(ValueError):
        _cache(_Clock(), max_concurrency=0)
