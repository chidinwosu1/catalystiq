"""Tests for get_broker_provider() - the factory that must always resolve
to WebullBroker, with no fallback to any other provider (see
catalystiq/providers/broker.py's module docstring for the intended flow:
BrokerProvider -> WebullBroker -> Webull Trading API)."""
from unittest.mock import MagicMock

import pytest

from catalystiq.providers.broker import (
    AlpacaPaperBroker,
    BrokerError,
    WebullBroker,
    get_broker_provider,
)


@pytest.fixture(autouse=True)
def clear_settings_cache():
    from catalystiq.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _set_webull_env(monkeypatch):
    monkeypatch.setenv("BROKER_PROVIDER", "webull")
    monkeypatch.setenv("WEBULL_APP_KEY", "key")
    monkeypatch.setenv("WEBULL_APP_SECRET", "secret")
    monkeypatch.setenv("WEBULL_ACCOUNT_ID", "acct")


def test_webull_is_the_default_broker(monkeypatch):
    monkeypatch.delenv("BROKER_PROVIDER", raising=False)
    from catalystiq.config import Settings

    assert Settings().broker_provider == "webull"


def test_unsupported_broker_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("BROKER_PROVIDER", "alpaca")

    with pytest.raises(BrokerError, match="Unsupported BROKER_PROVIDER"):
        get_broker_provider()


def test_unknown_broker_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("BROKER_PROVIDER", "ibkr")

    with pytest.raises(BrokerError, match="Unsupported BROKER_PROVIDER"):
        get_broker_provider()


def test_does_not_fall_back_to_alpaca(monkeypatch):
    """Even with an unsupported BROKER_PROVIDER, AlpacaPaperBroker must
    never be constructed - the factory raises instead of silently falling
    back to any other provider."""
    monkeypatch.setenv("BROKER_PROVIDER", "alpaca")
    calls = []
    original_init = AlpacaPaperBroker.__init__

    def spy_init(self, *args, **kwargs):
        calls.append((args, kwargs))
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(AlpacaPaperBroker, "__init__", spy_init)

    with pytest.raises(BrokerError):
        get_broker_provider()

    assert calls == []


def test_get_broker_provider_returns_webull_broker(monkeypatch):
    _set_webull_env(monkeypatch)

    fake_api_client = MagicMock()
    fake_trade_client = MagicMock()
    monkeypatch.setattr("webull.core.client.ApiClient", lambda *a, **k: fake_api_client)
    monkeypatch.setattr("webull.trade.trade_client.TradeClient", lambda *a: fake_trade_client)

    broker = get_broker_provider()

    assert isinstance(broker, WebullBroker)


def test_missing_webull_credentials_raises_broker_error(monkeypatch):
    monkeypatch.setenv("BROKER_PROVIDER", "webull")
    monkeypatch.setenv("WEBULL_APP_KEY", "")
    monkeypatch.setenv("WEBULL_APP_SECRET", "")
    monkeypatch.setenv("WEBULL_ACCOUNT_ID", "")

    with pytest.raises(BrokerError, match="not configured"):
        get_broker_provider()
