"""Webull credentials are whitespace-stripped before signing, so a trailing
newline/space pasted into a hosting dashboard can't silently break the request
signature ("Header x-signature is invalid")."""
from __future__ import annotations

from catalystiq.providers.broker import WebullBroker


def test_credentials_are_stripped_before_use(monkeypatch):
    captured: dict[str, str] = {}

    class _FakeApiClient:
        def __init__(self, app_key, app_secret, region_id):
            captured["app_key"] = app_key
            captured["app_secret"] = app_secret
            captured["region_id"] = region_id

        def add_endpoint(self, *args, **kwargs):
            pass

        def set_token_dir(self, *args, **kwargs):
            pass

    monkeypatch.setattr("webull.core.client.ApiClient", _FakeApiClient)
    monkeypatch.setattr("webull.trade.trade_client.TradeClient", lambda client: object())

    broker = WebullBroker("  key\n", " secret \n", "\tacct \n", region_id=" us \n")

    assert captured["app_key"] == "key"
    assert captured["app_secret"] == "secret"
    assert captured["region_id"] == "us"
    assert broker._account_id == "acct"


def test_whitespace_only_credentials_still_rejected():
    import pytest

    from catalystiq.providers.broker import BrokerError

    with pytest.raises(BrokerError):
        WebullBroker("   ", "\n", "  ", region_id="us")
