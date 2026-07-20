"""Unit tests for the market-data (OHLCV/quote) governance gate: pass-through,
rate-limit cooldown open/recover, non-throttle errors, and concurrency limit."""
from __future__ import annotations

import threading
import time

import pytest

from catalystiq.providers.market_data import MarketDataError
from catalystiq.providers.market_data_gate import MarketDataGate


class _Clock:
    def __init__(self, start=1000.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def _gate(clock=None, **kw):
    params = dict(max_concurrency=4, rate_limit_threshold=2, cooldown_seconds=60.0)
    params.update(kw)
    if clock is not None:
        params["monotonic"] = clock
    return MarketDataGate(**params)


def test_gate_passes_result_through_and_counts():
    gate = _gate(_Clock())
    assert gate.run("ohlcv AAPL", lambda: 42) == 42
    assert gate.stats.provider_calls == 1
    assert not gate.cooldown_active


def test_gate_cooldown_opens_then_recovers():
    clock = _Clock()
    gate = _gate(clock, rate_limit_threshold=2, cooldown_seconds=60.0)

    def boom():
        raise MarketDataError("Too Many Requests. Rate limited")

    for _ in range(2):
        with pytest.raises(MarketDataError):
            gate.run("ohlcv NVDA", boom)
    assert gate.stats.rate_limited == 2

    # Cooldown open: fail fast, no provider call.
    with pytest.raises(MarketDataError) as excinfo:
        gate.run("ohlcv NVDA", boom)
    assert "cooldown" in str(excinfo.value).lower()
    assert gate.stats.provider_calls == 2
    assert gate.stats.cooldown_short_circuits == 1
    assert gate.cooldown_active

    clock.advance(61)
    assert gate.run("ohlcv NVDA", lambda: "ok") == "ok"
    assert not gate.cooldown_active


def test_gate_non_rate_limit_error_never_opens_cooldown():
    gate = _gate(_Clock(), rate_limit_threshold=1, cooldown_seconds=60.0)

    def boom():
        raise MarketDataError("empty history for ZZZZ")

    for _ in range(3):
        with pytest.raises(MarketDataError):
            gate.run("ohlcv ZZZZ", boom)
    assert gate.stats.rate_limited == 0
    assert not gate.cooldown_active


def test_gate_concurrency_limit_serializes():
    gate = _gate(_Clock(), max_concurrency=1)
    entered = threading.Semaphore(0)
    release = threading.Event()
    lock = threading.Lock()
    live = {"now": 0, "max": 0}

    def fn():
        with lock:
            live["now"] += 1
            live["max"] = max(live["max"], live["now"])
        entered.release()
        release.wait(5)
        with lock:
            live["now"] -= 1
        return None

    threads = [threading.Thread(target=lambda: gate.run("x", fn)) for _ in range(3)]
    for t in threads:
        t.start()
    assert entered.acquire(timeout=5)
    time.sleep(0.1)
    release.set()
    for t in threads:
        t.join(5)
    assert live["max"] == 1


def test_gate_requires_positive_concurrency():
    with pytest.raises(ValueError):
        _gate(_Clock(), max_concurrency=0)
