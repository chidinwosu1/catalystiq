"""A configured Webull base URL is normalized to the bare host the SDK expects,
so a pasted scheme/path (e.g. "https://api.sandbox.webull.com") can't silently
break the request signature."""
from __future__ import annotations

import pytest

from catalystiq.providers.broker import WebullBroker, _normalize_webull_host


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://api.sandbox.webull.com", "api.sandbox.webull.com"),
        ("http://api.sandbox.webull.com/", "api.sandbox.webull.com"),
        ("api.sandbox.webull.com", "api.sandbox.webull.com"),
        ("  https://api.sandbox.webull.com/openapi/  ", "api.sandbox.webull.com"),
        ("api.webull.com.", "api.webull.com"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_webull_host(raw, expected):
    assert _normalize_webull_host(raw) == expected


def test_scheme_stripped_before_add_endpoint(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeApiClient:
        def __init__(self, app_key, app_secret, region_id):
            pass

        def add_endpoint(self, region_id, base_url):
            captured["add_endpoint"] = (region_id, base_url)

        def set_token_dir(self, token_dir):
            pass

    monkeypatch.setattr("webull.core.client.ApiClient", _FakeApiClient)
    monkeypatch.setattr("webull.trade.trade_client.TradeClient", lambda client: object())

    # Pasted WITH scheme - must reach the SDK as a bare host.
    WebullBroker("key", "secret", "acct", region_id="us",
                 api_endpoint="https://api.sandbox.webull.com")

    assert captured["add_endpoint"] == ("us", "api.sandbox.webull.com")
