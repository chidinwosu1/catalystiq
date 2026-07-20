"""WebullBroker construction must surface any SDK/network failure as a
BrokerError (-> clean 502), never a raw exception that becomes an unhandled 500
the browser misreports as "Could not reach the API"."""
from __future__ import annotations

import pytest

from catalystiq.providers.broker import BrokerError, WebullBroker


def test_missing_credentials_raises_broker_error():
    with pytest.raises(BrokerError):
        WebullBroker("", "", "", region_id="us")


def test_sdk_construction_failure_wrapped_as_broker_error(monkeypatch):
    # Simulate the real failure mode: creds are set, but the SDK's network
    # token/config call at construction fails (invalid creds / blocked endpoint
    # / IP not allowlisted). It must come back as a BrokerError with a reason.
    monkeypatch.setattr("webull.core.client.ApiClient", lambda *a, **k: object())

    def boom(*args, **kwargs):
        raise ValueError("network token check failed")

    monkeypatch.setattr("webull.trade.trade_client.TradeClient", boom)

    with pytest.raises(BrokerError) as excinfo:
        WebullBroker("key", "secret", "acct", region_id="us")
    assert "Webull" in str(excinfo.value)


def test_import_error_wrapped_as_broker_error(monkeypatch):
    # If the SDK module can't be imported, that ImportError is also wrapped.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("webull"):
            raise ImportError("no webull sdk")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(BrokerError):
        WebullBroker("key", "secret", "acct", region_id="us")
