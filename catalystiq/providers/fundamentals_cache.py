"""Governed caching layer in front of ``MarketDataProvider.get_fundamentals``.

Why this exists
---------------
Yahoo's fundamentals endpoint (reached via ``yfinance``'s ``.info``) is NOT
behind the shared :class:`~catalystiq.providers.transport.HttpTransport`, so it
has none of that module's rate limiting or circuit breaking. It is also
aggressively **per-IP** rate limited. A burst of uncontrolled calls - most
notably the opportunity scan asking every universe symbol for its sector -
trips a 429 that then makes *every* subsequent lookup (any ticker) fail until
the throttle clears (see NVDA_RATE_LIMIT_DIAGNOSIS.md).

This layer sits in front of a provider's ``get_fundamentals`` and adds, per
provider:

  - a **TTL cache** (fundamentals change slowly; default hours). A cache hit
    returns the *originally fetched* snapshot unchanged, so its ``as_of``
    remains the true point-in-time it was retrieved - never re-stamped.
  - **single-flight de-duplication**: concurrent identical in-flight calls
    share one fetch instead of each hitting Yahoo.
  - a **concurrency limit** (semaphore) so a burst is paced, not parallel.
  - a **circuit-breaker cooldown**: after N consecutive rate-limited fetches it
    fails fast (no provider call) for a cooldown window, then allows a single
    trial. Only 429/rate-limit failures count toward it - a normal error
    (e.g. unknown symbol) never opens it.
  - **call statistics** for instrumentation / before-after measurement.

Everything time-dependent (clock) is injectable, so the whole module is unit
testable with no real network and no real waiting.

Nothing here fabricates data: a miss that can't be fetched raises; it never
invents a snapshot.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

from catalystiq.providers.market_data import FundamentalsSnapshot, MarketDataError
from catalystiq.providers.transport import CircuitBreaker

# Substrings (case-insensitive) that mark a fetch failure as an upstream
# rate-limit. yfinance surfaces Yahoo 429s as "Too Many Requests. Rate
# limited ..."; we also match a bare "429".
_RATE_LIMIT_MARKERS = ("too many requests", "rate limit", "429")


def is_rate_limited_error(exc: BaseException) -> bool:
    """Whether ``exc`` looks like an upstream (provider) rate-limit / 429.
    Only these trip the cooldown; ordinary errors don't."""
    text = str(exc).lower()
    return any(marker in text for marker in _RATE_LIMIT_MARKERS)


@dataclass
class FundamentalsCacheStats:
    """Counters for instrumentation. ``provider_calls`` is the number of real
    fetches attempted against the upstream provider (the request count we are
    trying to reduce); the rest are calls that avoided one."""

    calls_total: int = 0
    provider_calls: int = 0
    cache_hits: int = 0
    dedup_coalesced: int = 0
    cooldown_short_circuits: int = 0
    rate_limited: int = 0
    errors: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "calls_total": self.calls_total,
            "provider_calls": self.provider_calls,
            "cache_hits": self.cache_hits,
            "dedup_coalesced": self.dedup_coalesced,
            "cooldown_short_circuits": self.cooldown_short_circuits,
            "rate_limited": self.rate_limited,
            "errors": self.errors,
        }


@dataclass
class _Entry:
    snapshot: FundamentalsSnapshot
    stored_at: float


class _Holder:
    """Shared result slot for a single in-flight fetch, so coalesced waiters
    receive the same snapshot (or the same exception)."""

    __slots__ = ("event", "snapshot", "exc")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.snapshot: FundamentalsSnapshot | None = None
        self.exc: BaseException | None = None


def _default_monotonic() -> float:
    import time

    return time.monotonic()


class FundamentalsCache:
    """Per-provider governed cache. Thread-safe (FastAPI runs sync endpoints in
    a threadpool). ``fetch`` is passed per call so the cache never binds to a
    stale provider instance; only the *owning* call of a single-flight group
    actually invokes it."""

    def __init__(
        self,
        *,
        ttl_seconds: float,
        max_concurrency: int,
        rate_limit_threshold: int,
        cooldown_seconds: float,
        monotonic: Callable[[], float] = _default_monotonic,
        is_rate_limited: Callable[[BaseException], bool] = is_rate_limited_error,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._ttl = ttl_seconds
        self._monotonic = monotonic
        self._is_rate_limited = is_rate_limited
        self._lock = threading.Lock()
        self._cache: dict[str, _Entry] = {}
        self._inflight: dict[str, _Holder] = {}
        self._semaphore = threading.BoundedSemaphore(max_concurrency)
        # Only rate-limit failures are recorded against the breaker, so
        # `failure_threshold` counts consecutive 429s; `reset_timeout` is the
        # cooldown before a single trial is allowed again.
        self._breaker = CircuitBreaker(
            failure_threshold=rate_limit_threshold,
            reset_timeout_sec=cooldown_seconds,
            monotonic=monotonic,
        )
        self.stats = FundamentalsCacheStats()

    def _expired(self, entry: _Entry) -> bool:
        return (self._monotonic() - entry.stored_at) >= self._ttl

    @property
    def cooldown_active(self) -> bool:
        with self._lock:
            return not self._breaker.allow()

    def get(
        self, symbol: str, fetch: Callable[[str], FundamentalsSnapshot]
    ) -> FundamentalsSnapshot:
        symbol = symbol.upper()
        with self._lock:
            self.stats.calls_total += 1
            entry = self._cache.get(symbol)
            if entry is not None and not self._expired(entry):
                self.stats.cache_hits += 1
                return entry.snapshot

            waiting_on = self._inflight.get(symbol)
            if waiting_on is not None:
                # An identical fetch is already running - coalesce onto it.
                self.stats.dedup_coalesced += 1
            else:
                # We own the fetch. Decide breaker admission under the lock.
                waiting_on = None
                holder = _Holder()
                self._inflight[symbol] = holder
                admitted = self._breaker.allow()

        if waiting_on is not None:
            waiting_on.event.wait()
            if waiting_on.exc is not None:
                raise waiting_on.exc
            assert waiting_on.snapshot is not None
            return waiting_on.snapshot

        # --- owning call ---------------------------------------------------
        try:
            if not admitted:
                with self._lock:
                    self.stats.cooldown_short_circuits += 1
                raise MarketDataError(
                    f"Fundamentals for {symbol} temporarily unavailable "
                    f"(upstream rate-limit cooldown)."
                )

            self._semaphore.acquire()
            try:
                with self._lock:
                    self.stats.provider_calls += 1
                snap = fetch(symbol)
            finally:
                self._semaphore.release()

            with self._lock:
                self._cache[symbol] = _Entry(snap, self._monotonic())
                self._breaker.record_success()
            holder.snapshot = snap
            return snap
        except BaseException as exc:  # noqa: BLE001 - re-raised after bookkeeping
            holder.exc = exc
            with self._lock:
                if self._is_rate_limited(exc):
                    self.stats.rate_limited += 1
                    self._breaker.record_failure()
                elif not isinstance(exc, MarketDataError) or "cooldown" not in str(exc):
                    # Don't double-count the cooldown short-circuit we raised.
                    self.stats.errors += 1
            raise
        finally:
            with self._lock:
                self._inflight.pop(symbol, None)
            holder.event.set()


# --- process-wide registry --------------------------------------------------
# One cache per provider name, shared across all requests/threads. Built lazily
# from settings the first time a provider is used.

_CACHES: dict[str, FundamentalsCache] = {}
_REGISTRY_LOCK = threading.Lock()


def _provider_key(provider) -> str:
    return getattr(provider, "PROVIDER_NAME", type(provider).__name__)


def _build_cache() -> FundamentalsCache:
    from catalystiq.config import get_settings

    s = get_settings()
    return FundamentalsCache(
        ttl_seconds=s.fundamentals_cache_ttl_seconds,
        max_concurrency=s.fundamentals_max_concurrency,
        rate_limit_threshold=s.fundamentals_rate_limit_threshold,
        cooldown_seconds=s.fundamentals_rate_limit_cooldown_seconds,
    )


def get_cache_for(provider) -> FundamentalsCache:
    key = _provider_key(provider)
    with _REGISTRY_LOCK:
        cache = _CACHES.get(key)
        if cache is None:
            cache = _build_cache()
            _CACHES[key] = cache
        return cache


def get_fundamentals_cached(provider, symbol: str) -> FundamentalsSnapshot:
    """Fetch fundamentals for ``symbol`` through the governed cache for
    ``provider``. Raises :class:`MarketDataError` on an unfetchable symbol or
    while the rate-limit cooldown is open - it never fabricates a snapshot."""
    cache = get_cache_for(provider)
    return cache.get(symbol, provider.get_fundamentals)


def fundamentals_cache_stats() -> dict[str, dict[str, int]]:
    """Snapshot of per-provider cache counters, for instrumentation."""
    with _REGISTRY_LOCK:
        return {name: cache.stats.as_dict() for name, cache in _CACHES.items()}


def reset_fundamentals_caches() -> None:
    """Drop all caches. Test-support only (isolate settings-dependent state)."""
    with _REGISTRY_LOCK:
        _CACHES.clear()
