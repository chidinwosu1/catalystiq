"""Tradovate futures adapter: auth token acquisition + caching, contract/product
mapping, 401 re-auth, time-penalty (p-ticket) fail-closed handling, NOT_FOUND on
empty lookups, missing-config handling, and the settings factory.

Offline - a fake transport stands in for HttpTransport, so there is no network
and no real waiting.
"""
from __future__ import annotations

import pytest

from catalystiq.providers.base import ProviderError, ProviderErrorCategory
from catalystiq.providers.tradovate import TradovateProvider, get_tradovate_provider


class _FakeResp:
    def __init__(self, body, status_code: int = 200):
        self._body = body
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            category = (
                ProviderErrorCategory.AUTH
                if self.status_code in (401, 403)
                else ProviderErrorCategory.UNAVAILABLE
            )
            raise ProviderError(
                f"HTTP {self.status_code}", category=category, provider="tradovate",
                status_code=self.status_code,
            )
        return self

    def json(self):
        return self._body


class _FakeTransport:
    """Returns queued responses in order and records every request so caching,
    headers and bodies can be asserted."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests: list[dict] = []

    def request(self, method, url, *, params=None, headers=None, json=None):
        self.requests.append(
            {"method": method, "url": url, "params": params, "headers": headers, "json": json}
        )
        if not self._responses:
            raise AssertionError(f"no queued response for {method} {url}")
        return self._responses.pop(0)


_AUTH_OK = _FakeResp({
    "accessToken": "tok-1",
    "mdAccessToken": "md-1",
    "expirationTime": "2026-01-01T00:00:00Z",
    "userStatus": "Active",
    "userId": 99,
})

_CONTRACT = {
    "id": 123,
    "name": "ESM5",
    "productId": 7,
    "contractMaturityId": 456,
    "status": "Active",
    "providerTickSize": 0.25,
}

_PRODUCT = {
    "id": 7,
    "name": "ES",
    "productType": "Futures",
    "description": "E-Mini S&P 500",
    "exchangeId": 2,
    "currencyId": 1,
    "valuePerPoint": 50.0,
    "tickSize": 0.25,
    "priceFormatType": "Decimal",
    "status": "Verified",
}


def _provider(responses, **kw):
    clock = kw.pop("clock", lambda: 1000.0)
    return TradovateProvider(
        username="u", password="p", cid="12345", sec="s",
        transport=_FakeTransport(responses), monotonic=clock, **kw,
    )


def test_find_contract_authenticates_then_maps():
    t = _FakeTransport([_AUTH_OK, _FakeResp(_CONTRACT)])
    p = TradovateProvider(username="u", password="p", cid="12345", sec="s", transport=t)
    contract = p.find_contract("esm5")

    assert contract.id == 123
    assert contract.name == "ESM5"
    assert contract.product_id == 7
    assert contract.contract_maturity_id == 456
    assert contract.tick_size == 0.25
    assert contract.status == "Active"

    # First call authenticated (POST with cid coerced to int, no deviceId).
    auth = t.requests[0]
    assert auth["method"] == "POST" and auth["url"] == "auth/accesstokenrequest"
    assert auth["json"]["cid"] == 12345 and "deviceId" not in auth["json"]
    # Second call is the bearer-authorized contract lookup, symbol upper-cased.
    lookup = t.requests[1]
    assert lookup["url"] == "contract/find"
    assert lookup["params"] == {"name": "ESM5"}
    assert lookup["headers"]["Authorization"] == "Bearer tok-1"


def test_token_is_cached_across_calls_then_refetched_after_ttl():
    clock = [1000.0]
    t = _FakeTransport([
        _AUTH_OK, _FakeResp(_PRODUCT),  # first product lookup (auth + get)
        _FakeResp(_PRODUCT),            # second lookup reuses cached token
        _AUTH_OK, _FakeResp(_PRODUCT),  # after TTL: re-auth + get
    ])
    p = TradovateProvider(
        username="u", password="p", cid="1", sec="s",
        transport=t, monotonic=lambda: clock[0], token_ttl_seconds=100,
    )
    p.find_product("ES")
    p.find_product("ES")
    # Only one auth so far (token reused).
    assert sum(1 for r in t.requests if r["url"] == "auth/accesstokenrequest") == 1

    clock[0] += 101  # past the token TTL
    p.find_product("ES")
    assert sum(1 for r in t.requests if r["url"] == "auth/accesstokenrequest") == 2


def test_find_product_maps_economics():
    p = _provider([_AUTH_OK, _FakeResp(_PRODUCT)])
    prod = p.find_product("es")
    assert prod.id == 7 and prod.name == "ES"
    assert prod.product_type == "Futures"
    assert prod.value_per_point == 50.0
    assert prod.tick_size == 0.25
    assert prod.exchange_id == 2


def test_list_products_skips_malformed_rows():
    body = [_PRODUCT, {"name": "no id"}, {"id": 8, "name": "NQ"}]
    p = _provider([_AUTH_OK, _FakeResp(body)])
    products = p.list_products()
    assert [pr.name for pr in products] == ["ES", "NQ"]


def test_find_contract_single_object_or_list_both_supported():
    # `find` may return a bare object or a one-element list; both normalize.
    p = _provider([_AUTH_OK, _FakeResp([_CONTRACT])])
    assert p.find_contract("ESM5").id == 123


def test_empty_lookup_raises_not_found():
    p = _provider([_AUTH_OK, _FakeResp([])])
    with pytest.raises(ProviderError) as exc:
        p.find_contract("NOPE")
    assert exc.value.category is ProviderErrorCategory.NOT_FOUND


def test_time_penalty_response_fails_closed():
    penalty = _FakeResp({"p-ticket": "abc", "p-time": 30})
    p = _provider([penalty])
    with pytest.raises(ProviderError) as exc:
        p.find_product("ES")
    assert exc.value.category is ProviderErrorCategory.RATE_LIMITED
    assert "30" in str(exc.value)


def test_tokenless_auth_response_is_auth_error():
    p = _provider([_FakeResp({"errorText": "Incorrect username or password"})])
    with pytest.raises(ProviderError) as exc:
        p.find_product("ES")
    assert exc.value.category is ProviderErrorCategory.AUTH
    assert "username or password" in str(exc.value)


def test_401_on_read_triggers_single_reauth_and_retry():
    t = _FakeTransport([
        _AUTH_OK,                    # initial auth
        _FakeResp(None, status_code=401),  # token rejected mid-session
        _AUTH_OK,                    # re-auth
        _FakeResp(_CONTRACT),        # retry succeeds
    ])
    p = TradovateProvider(username="u", password="p", cid="1", sec="s", transport=t)
    contract = p.find_contract("ESM5")
    assert contract.id == 123
    assert sum(1 for r in t.requests if r["url"] == "auth/accesstokenrequest") == 2


def test_device_id_included_when_set():
    t = _FakeTransport([_AUTH_OK, _FakeResp(_PRODUCT)])
    p = TradovateProvider(
        username="u", password="p", cid="1", sec="s", device_id="dev-9", transport=t,
    )
    p.find_product("ES")
    assert t.requests[0]["json"]["deviceId"] == "dev-9"


def test_missing_credentials_raise_config_error():
    with pytest.raises(ProviderError) as exc:
        TradovateProvider(username="", password="p", cid="1", sec="s")
    assert exc.value.category is ProviderErrorCategory.CONFIG
    assert "tradovate_username" in str(exc.value)


def test_environment_selects_demo_host_by_default():
    # No transport injected => the adapter builds its own against the demo host.
    p = TradovateProvider(username="u", password="p", cid="1", sec="s")
    assert p._transport.base_url == "https://demo.tradovateapi.com/v1"
    live = TradovateProvider(username="u", password="p", cid="1", sec="s", environment="live")
    assert live._transport.base_url == "https://live.tradovateapi.com/v1"


def test_factory_builds_from_settings(monkeypatch):
    class _S:
        tradovate_username = "u"
        tradovate_password = "p"
        tradovate_cid = "12345"
        tradovate_sec = "s"
        tradovate_app_id = "CatalystIQ"
        tradovate_app_version = "1.0"
        tradovate_device_id = ""
        tradovate_environment = "demo"
        tradovate_token_ttl_seconds = 4800

    monkeypatch.setattr("catalystiq.config.get_settings", lambda: _S())
    provider = get_tradovate_provider()
    assert provider.PROVIDER_NAME == "tradovate"
    assert provider._environment == "demo"


def test_factory_missing_credentials_raises(monkeypatch):
    class _S:
        tradovate_username = ""
        tradovate_password = ""
        tradovate_cid = ""
        tradovate_sec = ""
        tradovate_app_id = ""
        tradovate_app_version = "1.0"
        tradovate_device_id = ""
        tradovate_environment = "demo"
        tradovate_token_ttl_seconds = 4800

    monkeypatch.setattr("catalystiq.config.get_settings", lambda: _S())
    with pytest.raises(ProviderError) as exc:
        get_tradovate_provider()
    assert exc.value.category is ProviderErrorCategory.CONFIG
