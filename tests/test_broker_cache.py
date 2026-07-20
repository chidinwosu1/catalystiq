"""The per-request broker factory caches its constructed WebullBroker so each
`/paper/*` call doesn't rebuild (and re-network-auth) the Webull client.

These exercise `get_broker_provider` in isolation by stubbing both the settings
and `WebullBroker` construction, so they run without live credentials or the
network. See `catalystiq/providers/broker.py`.
"""
from __future__ import annotations

import types

import pytest

import catalystiq.providers.broker as broker_mod
from catalystiq.providers.broker import (
    BrokerError,
    get_broker_provider,
    reset_broker_cache,
)


def _fake_settings(**overrides):
    base = dict(
        broker_provider="webull",
        webull_app_key="key",
        webull_app_secret="secret",
        webull_account_id="acct",
        webull_region_id="us",
        webull_api_base_url="",
        webull_token_dir="",
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _clean_cache():
    reset_broker_cache()
    yield
    reset_broker_cache()


def _patch(monkeypatch, settings, construct):
    """Route get_settings() and WebullBroker(...) to test doubles."""
    import catalystiq.config as config

    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(broker_mod, "WebullBroker", construct)


def test_broker_is_built_once_and_reused(monkeypatch):
    calls = {"n": 0}

    def construct(*args, **kwargs):
        calls["n"] += 1
        return object()  # stand-in for a WebullBroker instance

    _patch(monkeypatch, _fake_settings(), construct)

    first = get_broker_provider()
    second = get_broker_provider()
    third = get_broker_provider()

    assert first is second is third  # same cached instance
    assert calls["n"] == 1  # constructed exactly once, not per call


def test_construction_failure_is_not_cached(monkeypatch):
    calls = {"n": 0}

    def construct(*args, **kwargs):
        calls["n"] += 1
        raise BrokerError("Webull app_key, app_secret, and account_id are not configured.")

    _patch(monkeypatch, _fake_settings(webull_app_key=""), construct)

    # A missing-credential failure must surface on EVERY request (the 502 path),
    # never be cached and then hidden.
    with pytest.raises(BrokerError):
        get_broker_provider()
    with pytest.raises(BrokerError):
        get_broker_provider()
    assert calls["n"] == 2


def test_unsupported_provider_never_constructs(monkeypatch):
    calls = {"n": 0}

    def construct(*args, **kwargs):
        calls["n"] += 1
        return object()

    _patch(monkeypatch, _fake_settings(broker_provider="alpaca"), construct)

    with pytest.raises(BrokerError, match="Unsupported BROKER_PROVIDER"):
        get_broker_provider()
    assert calls["n"] == 0


def test_different_credentials_get_distinct_instances(monkeypatch):
    built = []

    def construct(*args, **kwargs):
        inst = object()
        built.append(inst)
        return inst

    # First account.
    _patch(monkeypatch, _fake_settings(webull_account_id="acct-A"), construct)
    a1 = get_broker_provider()
    a2 = get_broker_provider()

    # Switch credentials (e.g. settings reloaded) → a new instance, and the old
    # one is not served for the new key.
    _patch(monkeypatch, _fake_settings(webull_account_id="acct-B"), construct)
    b1 = get_broker_provider()

    assert a1 is a2
    assert b1 is not a1
    assert len(built) == 2


def test_reset_clears_cache(monkeypatch):
    calls = {"n": 0}

    def construct(*args, **kwargs):
        calls["n"] += 1
        return object()

    _patch(monkeypatch, _fake_settings(), construct)

    get_broker_provider()
    assert calls["n"] == 1
    reset_broker_cache()
    get_broker_provider()  # rebuilds after reset
    assert calls["n"] == 2
