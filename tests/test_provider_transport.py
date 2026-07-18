"""Shared HTTP transport: rate limiter, circuit breaker, retry/backoff,
error categorization, and secret redaction - all exercised with a fake HTTP
client and a fake clock, so no network and no real sleeping."""
import httpx
import pytest

from catalystiq.providers.base import ProviderError, ProviderErrorCategory
from catalystiq.providers.transport import (
    CircuitBreaker,
    HttpTransport,
    RateLimiter,
    redact,
)


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, d: float) -> None:
        self.sleeps.append(d)
        self.t += d


class FakeResponse:
    def __init__(self, status_code, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class FakeClient:
    """Serves a scripted sequence of FakeResponse / raised exceptions."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def request(self, method, url, params=None, headers=None, json=None, timeout=None):
        self.calls.append(
            {"method": method, "url": url, "params": params, "headers": headers, "timeout": timeout}
        )
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _transport(client, clock=None, **kw):
    clock = clock or FakeClock()
    kw.setdefault("max_retries", 3)
    return HttpTransport(
        "testprov",
        base_url="https://example.test",
        client=client,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        rand=lambda: 0.5,
        **kw,
    ), clock


# --- redaction -----------------------------------------------------------

def test_redact_masks_secret_keys_case_insensitively():
    out = redact({"api_key": "TOPSECRET", "Authorization": "Bearer x", "symbol": "AAPL"})
    assert out["api_key"] == "***"
    assert out["Authorization"] == "***"
    assert out["symbol"] == "AAPL"


def test_redact_none_is_empty_dict():
    assert redact(None) == {}


# --- rate limiter --------------------------------------------------------

def test_rate_limiter_sleeps_when_bucket_empty():
    clock = FakeClock()
    rl = RateLimiter(rate_per_sec=2.0, capacity=2.0, monotonic=clock.monotonic, sleep=clock.sleep)
    rl.acquire()  # 2 -> 1
    rl.acquire()  # 1 -> 0
    assert clock.sleeps == []
    rl.acquire()  # empty -> must wait 0.5s for one token at 2/sec
    assert clock.sleeps and clock.sleeps[0] == pytest.approx(0.5)


def test_rate_limiter_rejects_nonpositive_rate():
    with pytest.raises(ValueError):
        RateLimiter(0)


# --- circuit breaker -----------------------------------------------------

def test_circuit_breaker_opens_and_recovers():
    clock = FakeClock()
    cb = CircuitBreaker(failure_threshold=2, reset_timeout_sec=5.0, monotonic=clock.monotonic)
    assert cb.allow() is True
    cb.record_failure()
    assert cb.allow() is True
    cb.record_failure()  # threshold reached
    assert cb.state == "open"
    assert cb.allow() is False  # short-circuits
    clock.t += 5.0
    assert cb.allow() is True  # half-open trial
    cb.record_success()
    assert cb.state == "closed"


# --- transport happy path + envelope ------------------------------------

def test_request_success_returns_envelope():
    client = FakeClient([FakeResponse(200, text='{"ok": true}', headers={"X-RateLimit-Remaining": "9"})])
    transport, _ = _transport(client)
    resp = transport.request("GET", "/data", params={"api_key": "SECRET", "id": "AAPL"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert resp.retry_count == 0
    assert resp.rate_limit.get("x-ratelimit-remaining") == "9"
    # The secret param was still sent to the client (real request), just never logged.
    assert client.calls[0]["params"]["api_key"] == "SECRET"


def test_request_retries_5xx_then_succeeds():
    client = FakeClient([FakeResponse(503), FakeResponse(503), FakeResponse(200, text="{}")])
    transport, clock = _transport(client)
    resp = transport.request("GET", "/x")
    assert resp.status_code == 200
    assert resp.retry_count == 2
    assert len(clock.sleeps) == 2  # backed off twice


def test_request_final_retryable_status_returns_envelope():
    # A retryable HTTP status that persists through every attempt comes back
    # as the final envelope (the server DID answer, repeatedly) rather than
    # raising - the caller decides via raise_for_status(). Contrast with
    # transport errors (timeout/network), which raise on exhaustion.
    client = FakeClient([FakeResponse(503), FakeResponse(503), FakeResponse(503), FakeResponse(503)])
    transport, clock = _transport(client, max_retries=3)
    resp = transport.request("GET", "/x")
    assert resp.status_code == 503
    assert resp.retry_count == 3
    assert len(clock.sleeps) == 3  # backed off before each of the 3 retries
    with pytest.raises(ProviderError) as exc:
        resp.raise_for_status()
    assert exc.value.category is ProviderErrorCategory.UNAVAILABLE


def test_request_429_respects_retry_after_then_succeeds():
    client = FakeClient([FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse(200, text="{}")])
    transport, clock = _transport(client)
    resp = transport.request("GET", "/x")
    assert resp.status_code == 200
    assert clock.sleeps == [7.0]  # honored Retry-After exactly (below cap)


def test_request_timeout_retried_then_raised():
    client = FakeClient([httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"),
                         httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow")])
    transport, _ = _transport(client, max_retries=3)
    with pytest.raises(ProviderError) as exc:
        transport.request("GET", "/x")
    assert exc.value.category is ProviderErrorCategory.TIMEOUT


def test_request_network_error_categorized():
    client = FakeClient([httpx.ConnectError("no route"), httpx.ConnectError("no route"),
                         httpx.ConnectError("no route"), httpx.ConnectError("no route")])
    transport, _ = _transport(client, max_retries=3)
    with pytest.raises(ProviderError) as exc:
        transport.request("GET", "/x")
    assert exc.value.category is ProviderErrorCategory.NETWORK


def test_404_returned_as_envelope_and_raise_for_status_maps_not_found():
    client = FakeClient([FakeResponse(404, text="nope")])
    transport, _ = _transport(client)
    resp = transport.request("GET", "/missing")
    assert resp.status_code == 404  # not retried, returned as envelope
    with pytest.raises(ProviderError) as exc:
        resp.raise_for_status()
    assert exc.value.category is ProviderErrorCategory.NOT_FOUND


def test_401_maps_to_auth_and_is_not_retried():
    client = FakeClient([FakeResponse(401, text="denied")])
    transport, clock = _transport(client)
    resp = transport.request("GET", "/x")
    assert resp.status_code == 401
    assert clock.sleeps == []  # auth is terminal, never retried
    with pytest.raises(ProviderError) as exc:
        resp.raise_for_status()
    assert exc.value.category is ProviderErrorCategory.AUTH


def test_malformed_json_raises_malformed_category():
    client = FakeClient([FakeResponse(200, text="not json")])
    transport, _ = _transport(client)
    resp = transport.request("GET", "/x")
    with pytest.raises(ProviderError) as exc:
        resp.json()
    assert exc.value.category is ProviderErrorCategory.MALFORMED_RESPONSE


def test_circuit_opens_after_repeated_transport_failures():
    # 3 separate exhausting calls, threshold default 5 => not open yet;
    # a 5th failure opens it. Each request() records exactly one breaker
    # failure on exhaustion.
    breaker = CircuitBreaker(failure_threshold=2, reset_timeout_sec=100.0)
    err = httpx.ConnectError("x")
    for _ in range(2):
        client = FakeClient([err, err, err, err])
        transport = HttpTransport(
            "p", client=client, sleep=lambda d: None, rand=lambda: 0.0,
            circuit_breaker=breaker, max_retries=3,
        )
        with pytest.raises(ProviderError):
            transport.request("GET", "https://x.test/y")
    # Breaker now open; next call fails fast with UNAVAILABLE before any HTTP.
    client = FakeClient([FakeResponse(200, text="{}")])
    transport = HttpTransport("p", client=client, circuit_breaker=breaker)
    with pytest.raises(ProviderError) as exc:
        transport.request("GET", "https://x.test/y")
    assert exc.value.category is ProviderErrorCategory.UNAVAILABLE
    assert client.calls == []  # never reached the client
