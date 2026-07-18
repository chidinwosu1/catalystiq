"""Shared HTTP transport for the network-backed provider adapters.

The two adapters that exist today (Yahoo via yfinance, Webull via its SDK)
own their own HTTP through third-party libraries and do NOT use this. This
module exists for the Phase 2+ adapters that call REST endpoints directly -
SEC EDGAR, FRED/ALFRED, BLS, BEA, FINRA, Nasdaq Trader, Twelve Data - so
every one of them gets the same, tested reliability behavior the spec
requires (§1, §19) instead of re-implementing it per adapter:

  - explicit connect + read timeouts,
  - bounded retries with exponential backoff and jitter,
  - a per-provider rate limiter (token bucket),
  - a circuit breaker so a downed provider fails fast instead of hanging,
  - a normalized error taxonomy (ProviderError + ProviderErrorCategory),
  - secret redaction for anything that gets logged.

Everything time-dependent (the clock, sleeping, jitter) is injectable, so
the whole module is unit-testable with no real network and no real waiting
(see tests). Nothing here parses a specific provider's payload - adapters do
that on top of the returned HttpResponse envelope.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from catalystiq.providers.base import ProviderError, ProviderErrorCategory

logger = logging.getLogger(__name__)

# Header/param keys whose *values* are secrets and must never be logged.
_REDACT_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "api-key",
    "x-api-key",
    "token",
    "access_token",
    "secret",
    "app_key",
    "app_secret",
    "appkey",
    "appsecret",
    "key",
    "password",
    "registration_token",
    "userid",
    "account_id",
}

_REDACTED = "***"

# HTTP statuses worth retrying (transient server-side / throttling).
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


def redact(data: dict | None) -> dict:
    """Copy of `data` with secret-bearing values masked. Case-insensitive on
    keys. Used before any params/headers reach a log line."""
    if not data:
        return {}
    out: dict = {}
    for k, v in data.items():
        out[k] = _REDACTED if str(k).lower() in _REDACT_KEYS else v
    return out


class RateLimiter:
    """Token-bucket limiter. `rate_per_sec` tokens refill continuously up to
    `capacity`; `acquire()` sleeps just long enough when the bucket is empty.
    Clock and sleep are injected so tests run instantly and deterministically."""

    def __init__(
        self,
        rate_per_sec: float,
        capacity: float | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be positive")
        self.rate = rate_per_sec
        self.capacity = capacity if capacity is not None else max(1.0, rate_per_sec)
        self._tokens = self.capacity
        self._monotonic = monotonic
        self._sleep = sleep
        self._last = monotonic()

    def _refill(self) -> None:
        now = self._monotonic()
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)

    def acquire(self, tokens: float = 1.0) -> None:
        # Loop rather than compute-and-sleep-once so an injected fake clock
        # that doesn't advance during sleep still terminates correctly.
        while True:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return
            wait = (tokens - self._tokens) / self.rate
            self._sleep(wait)


class CircuitBreaker:
    """Opens after `failure_threshold` consecutive failures and short-circuits
    further calls for `reset_timeout_sec`, then allows a single half-open
    trial. A success closes it; a failure re-opens it."""

    def __init__(
        self,
        failure_threshold: int = 5,
        reset_timeout_sec: float = 30.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.reset_timeout_sec = reset_timeout_sec
        self._monotonic = monotonic
        self._failures = 0
        self._opened_at: float | None = None
        self.state = "closed"

    def allow(self) -> bool:
        if self.state == "open":
            assert self._opened_at is not None
            if self._monotonic() - self._opened_at >= self.reset_timeout_sec:
                self.state = "half_open"
                return True
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self.state = "closed"

    def record_failure(self) -> None:
        self._failures += 1
        if self.state == "half_open" or self._failures >= self.failure_threshold:
            self.state = "open"
            self._opened_at = self._monotonic()


@dataclass
class HttpResponse:
    """Normalized result envelope. `rate_limit` is a best-effort scrape of any
    x-ratelimit-* / x-rate-* headers, for the ingestion record and health
    reporting (§3, §19)."""

    status_code: int
    headers: dict[str, str]
    text: str
    url: str
    elapsed_ms: float
    retry_count: int
    provider: str
    rate_limit: dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        import json as _json

        try:
            return _json.loads(self.text)
        except ValueError as exc:
            raise ProviderError(
                f"{self.provider}: response was not valid JSON.",
                category=ProviderErrorCategory.MALFORMED_RESPONSE,
                provider=self.provider,
                status_code=self.status_code,
            ) from exc

    def raise_for_status(self) -> "HttpResponse":
        """Raise a categorized ProviderError for a 4xx/5xx that the transport
        chose to return (auth/not-found are non-retryable, so they come back
        as envelopes rather than being retried). 2xx/3xx pass through."""
        if self.status_code < 400:
            return self
        if self.status_code in (401, 403):
            category = ProviderErrorCategory.AUTH
        elif self.status_code == 404:
            category = ProviderErrorCategory.NOT_FOUND
        elif self.status_code == 429:
            category = ProviderErrorCategory.RATE_LIMITED
        elif self.status_code >= 500:
            category = ProviderErrorCategory.UNAVAILABLE
        else:
            category = ProviderErrorCategory.UNKNOWN
        raise ProviderError(
            f"{self.provider}: HTTP {self.status_code} for {self.url}.",
            category=category,
            provider=self.provider,
            status_code=self.status_code,
        )


def _extract_rate_limit(headers: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in headers.items():
        lk = k.lower()
        if "ratelimit" in lk or "rate-limit" in lk or lk == "retry-after":
            out[lk] = v
    return out


class HttpTransport:
    """A per-provider HTTP client wrapping the reliability primitives above.

    `client` is any object exposing `request(method, url, params=, headers=,
    json=, timeout=)` and returning something with `.status_code`, `.headers`
    (a mapping), and `.text` - httpx.Client satisfies this, and tests inject a
    fake. It's created lazily from httpx if not supplied, so importing this
    module never requires a live client."""

    def __init__(
        self,
        provider: str,
        base_url: str = "",
        *,
        connect_timeout: float = 5.0,
        read_timeout: float = 30.0,
        max_retries: int = 3,
        backoff_base_sec: float = 0.5,
        backoff_max_sec: float = 30.0,
        default_headers: dict[str, str] | None = None,
        rate_limiter: RateLimiter | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rand: Callable[[], float] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.timeout = (connect_timeout, read_timeout)
        self.max_retries = max_retries
        self.backoff_base_sec = backoff_base_sec
        self.backoff_max_sec = backoff_max_sec
        self.default_headers = default_headers or {}
        self._rate_limiter = rate_limiter
        self._breaker = circuit_breaker or CircuitBreaker(monotonic=monotonic)
        self._client = client
        self._sleep = sleep
        self._monotonic = monotonic
        if rand is None:
            import random

            rand = random.random
        self._rand = rand

    def _get_client(self):
        if self._client is None:
            import httpx

            self._client = httpx.Client()
        return self._client

    def _full_url(self, url: str) -> str:
        if url.startswith("http://") or url.startswith("https://"):
            return url
        if not self.base_url:
            return url
        return f"{self.base_url}/{url.lstrip('/')}"

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(self.backoff_max_sec, retry_after)
        # Full jitter: uniform in [0, min(cap, base * 2**attempt)].
        ceiling = min(self.backoff_max_sec, self.backoff_base_sec * (2 ** attempt))
        return self._rand() * ceiling

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        json: Any | None = None,
    ) -> HttpResponse:
        """Perform an HTTP request with retries/backoff/rate-limiting/circuit
        breaking. Returns an HttpResponse for any received HTTP status
        (including 4xx - the caller decides via raise_for_status()); raises
        ProviderError only when the circuit is open, retries are exhausted on
        a transient failure, or a non-HTTP transport error occurs. Secrets in
        params/headers are never logged."""
        import httpx

        if not self._breaker.allow():
            raise ProviderError(
                f"{self.provider}: circuit breaker is open - failing fast.",
                category=ProviderErrorCategory.UNAVAILABLE,
                provider=self.provider,
            )

        full_url = self._full_url(url)
        merged_headers = {**self.default_headers, **(headers or {})}
        client = self._get_client()
        last_category = ProviderErrorCategory.UNKNOWN
        last_detail = ""

        for attempt in range(self.max_retries + 1):
            if self._rate_limiter is not None:
                self._rate_limiter.acquire()

            started = self._monotonic()
            retry_after: float | None = None
            try:
                raw = client.request(
                    method,
                    full_url,
                    params=params,
                    headers=merged_headers,
                    json=json,
                    timeout=self.timeout,
                )
            except httpx.TimeoutException as exc:
                last_category, last_detail = ProviderErrorCategory.TIMEOUT, str(exc)
            except httpx.TransportError as exc:
                # ConnectError, ReadError, NetworkError, etc.
                last_category, last_detail = ProviderErrorCategory.NETWORK, str(exc)
            else:
                elapsed_ms = (self._monotonic() - started) * 1000.0
                resp_headers = {str(k).lower(): str(v) for k, v in dict(raw.headers).items()}
                status = raw.status_code

                if status not in _RETRYABLE_STATUSES:
                    # Final HTTP answer (2xx/3xx or non-retryable 4xx). The
                    # endpoint responded, so the breaker is healthy - even a
                    # 404 means "reachable", not "provider down".
                    self._breaker.record_success()
                    return HttpResponse(
                        status_code=status,
                        headers=resp_headers,
                        text=raw.text,
                        url=full_url,
                        elapsed_ms=elapsed_ms,
                        retry_count=attempt,
                        provider=self.provider,
                        rate_limit=_extract_rate_limit(resp_headers),
                    )

                # Retryable status.
                last_category = (
                    ProviderErrorCategory.RATE_LIMITED
                    if status == 429
                    else ProviderErrorCategory.UNAVAILABLE
                )
                last_detail = f"HTTP {status}"
                ra = resp_headers.get("retry-after")
                if ra is not None:
                    try:
                        retry_after = float(ra)
                    except ValueError:
                        retry_after = None
                if attempt >= self.max_retries:
                    self._breaker.record_failure()
                    return HttpResponse(
                        status_code=status,
                        headers=resp_headers,
                        text=raw.text,
                        url=full_url,
                        elapsed_ms=elapsed_ms,
                        retry_count=attempt,
                        provider=self.provider,
                        rate_limit=_extract_rate_limit(resp_headers),
                    )

            # Reached here => this attempt failed and we may retry.
            if attempt >= self.max_retries:
                break
            delay = self._backoff(attempt, retry_after)
            logger.warning(
                "provider=%s attempt=%d/%d category=%s retrying_in=%.2fs params=%s",
                self.provider,
                attempt + 1,
                self.max_retries + 1,
                last_category.value,
                delay,
                redact(params),
            )
            self._sleep(delay)

        self._breaker.record_failure()
        raise ProviderError(
            f"{self.provider}: request failed after {self.max_retries + 1} attempts "
            f"({last_category.value}): {last_detail}",
            category=last_category,
            provider=self.provider,
        )
