"""The configured Webull API base URL is passed to the SDK's add_endpoint, and
paper trading defaults to the sandbox host."""
from __future__ import annotations

from catalystiq.config import Settings
from catalystiq.providers.broker import WebullBroker


def test_default_base_url_is_sandbox():
    assert Settings().webull_api_base_url == "api.sandbox.webull.com"


def test_base_url_passed_to_add_endpoint(monkeypatch):
    calls: dict[str, object] = {}

    class _FakeApiClient:
        def __init__(self, app_key, app_secret, region_id):
            calls["init"] = (app_key, app_secret, region_id)

        def add_endpoint(self, region_id, base_url):
            calls["add_endpoint"] = (region_id, base_url)

        def set_token_dir(self, token_dir):
            calls["token_dir"] = token_dir

    monkeypatch.setattr("webull.core.client.ApiClient", _FakeApiClient)
    monkeypatch.setattr("webull.trade.trade_client.TradeClient", lambda client: object())

    WebullBroker("key", "secret", "acct", region_id="us", api_endpoint="api.sandbox.webull.com")

    assert calls["init"] == ("key", "secret", "us")
    # Bare host, region-scoped, exactly as the SDK expects.
    assert calls["add_endpoint"] == ("us", "api.sandbox.webull.com")
