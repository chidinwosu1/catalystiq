"""The Webull diagnostics helper reports config for debugging without leaking
secrets, and resolves the trade host so the sandbox override can be confirmed."""
from __future__ import annotations

import catalystiq.config as cfg
import catalystiq.providers.broker as bk
from catalystiq.config import Settings


def _settings():
    return Settings(
        webull_app_key="abcd1234efgh5678ijkl",
        webull_app_secret="secretsecretsecretsecret",
        webull_account_id="DEM123456",
        webull_api_base_url="api.sandbox.webull.com",
        webull_region_id="us",
    )


def _raise_auth():
    raise bk.BrokerError("HTTP 401 ... x-signature invalid")


def test_diagnostics_masks_secrets_and_resolves_host(monkeypatch):
    monkeypatch.setattr(cfg, "get_settings", _settings)
    # Avoid a real network call during the init probe.
    monkeypatch.setattr(bk, "get_broker_provider", _raise_auth)

    d = bk.webull_diagnostics()

    # The sandbox override resolves for the trade api_type ("api").
    assert d["resolved_trade_host"] == "api.sandbox.webull.com"
    assert d["region_id"] == "us"
    assert d["signer"].startswith("HMAC-SHA256")

    # app_key: masked preview + length (catches truncation/whitespace).
    assert d["app_key"]["preview"] == "abcd…ijkl"
    assert d["app_key"]["length"] == 20

    # app_secret: NEVER previewed - length only.
    assert "preview" not in d["app_secret"]
    assert d["app_secret"]["set"] is True
    assert d["app_secret"]["length"] == 24

    # The raw secret must not appear anywhere in the payload.
    import json

    assert "secretsecretsecretsecret" not in json.dumps(d)

    # The real init error is surfaced.
    assert d["init_ok"] is False
    assert "x-signature" in d["init_error"]


def test_diagnostics_endpoint_returns_masked_json(client, monkeypatch):
    monkeypatch.setattr(cfg, "get_settings", _settings)
    monkeypatch.setattr(bk, "get_broker_provider", _raise_auth)

    r = client.get("/paper/webull-diagnostics")
    assert r.status_code == 200
    body = r.json()
    assert body["resolved_trade_host"] == "api.sandbox.webull.com"
    assert "preview" not in body["app_secret"]
