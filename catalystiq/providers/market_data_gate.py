"""Governance gate for the market-data (OHLCV/quote) ingest path.

Unlike fundamentals, OHLCV/quotes are not result-cached here (Silver is their
store), but they hit the same throttled Yahoo endpoints via ``yfinance`` with no
shared rate limiting or circuit breaking. On a cold cache an opportunity scan
ingests 5 years of history for ~30 symbols *sequentially*; under Yahoo
throttling each call retries/backs off and the whole request hangs.

This gate wraps the provider ingest calls with, per provider:

  - a **concurrency limit** (semaphore) so concurrent requests / the background
    warmer don't fan out in parallel against one throttled IP, and
  - a **rate-limit circuit-breaker cooldown**: after N consecutive 429s it
    fails fast (no provider call) for a cooldown window, then allows a single
    trial. Only rate-limit failures trip it; a normal error (bad symbol,
    empty history) never opens it.

It does NOT add an artificial per-call sleep - the win comes from the breaker
(fail fast instead of hang) plus the background warmer keeping Silver fresh so
the user path rarely fetches at all. Everything time-dependent is injectable.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, TypeVar

from catalystiq.providers.fundamentals_cache import is_rate_limited_error
from catalystiq.providers.market_data import MarketDataError
from catalystiq.providers.transport import CircuitBreaker

T = TypeVar("T")


@dataclass
class MarketDataGateStats:
    calls_total: int = 0
    provider_calls: int = 0
    cooldown_short_circuits: int = 0
    rate_limited: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "calls_total": self.calls_total,
            "provider_calls": self.provider_calls,
            "cooldown_short_circuits": self.cooldown_short_circuits,
            "rate_limited": self.rate_limited,
        }


def _default_monotonic() -> float:
    import time

    return time.monotonic()


class MarketDataGate:
    """Per-provider concurrency + rate-limit-cooldown gate. Thread-safe."""

    def __init__(
        self,
        *,
        max_concurrency: int,
        rate_limit_threshold: int,
        cooldown_seconds: float,
        monotonic: Callable[[], float] = _default_monotonic,
        is_rate_limited: Callable[[BaseException], bool] = is_rate_limited_error,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._lock = threading.Lock()
        self._semaphore = threading.BoundedSemaphore(max_concurrency)
        self._breaker = CircuitBreaker(
            failure_threshold=rate_limit_threshold,
            reset_timeout_sec=cooldown_seconds,
            monotonic=monotonic,
        )
        self._is_rate_limited = is_rate_limited
        self.stats = MarketDataGateStats()

    @property
    def cooldown_active(self) -> bool:
        with self._lock:
            return not self._breaker.allow()

    def run(self, label: str, fn: Callable[[], T]) -> T:
        """Run ``fn`` (a provider ingest call) under the gate. Raises
        :class:`MarketDataError` immediately if the cooldown is open, without
        calling ``fn``."""
        with self._lock:
            self.stats.calls_total += 1
            admitted = self._breaker.allow()
        if not admitted:
            with self._lock:
                self.stats.cooldown_short_circuits += 1
            raise MarketDataError(
                f"{label}: market data temporarily unavailable (upstream rate-limit cooldown)."
            )

        self._semaphore.acquire()
        try:
            with self._lock:
                self.stats.provider_calls += 1
            result = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised after bookkeeping
            with self._lock:
                if self._is_rate_limited(exc):
                    self.stats.rate_limited += 1
                    self._breaker.record_failure()
            raise
        else:
            with self._lock:
                self._breaker.record_success()
            return result
        finally:
            self._semaphore.release()


# --- process-wide registry --------------------------------------------------

_GATES: dict[str, MarketDataGate] = {}
_REGISTRY_LOCK = threading.Lock()


def _provider_key(provider) -> str:
    return getattr(provider, "PROVIDER_NAME", type(provider).__name__)


def _build_gate() -> MarketDataGate:
    from catalystiq.config import get_settings

    s = get_settings()
    return MarketDataGate(
        max_concurrency=s.market_data_max_concurrency,
        rate_limit_threshold=s.market_data_rate_limit_threshold,
        cooldown_seconds=s.market_data_rate_limit_cooldown_seconds,
    )


def get_gate_for(provider) -> MarketDataGate:
    key = _provider_key(provider)
    with _REGISTRY_LOCK:
        gate = _GATES.get(key)
        if gate is None:
            gate = _build_gate()
            _GATES[key] = gate
        return gate


def market_data_gate_stats() -> dict[str, dict[str, int]]:
    with _REGISTRY_LOCK:
        return {name: gate.stats.as_dict() for name, gate in _GATES.items()}


def reset_market_data_gates() -> None:
    """Drop all gates. Test-support only."""
    with _REGISTRY_LOCK:
        _GATES.clear()
