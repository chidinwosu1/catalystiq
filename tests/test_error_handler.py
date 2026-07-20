"""A non-HTTP error must come back as a CORS-headed 500 so the browser can
READ it (and show the real error) instead of the fetch throwing and the UI
reporting a misleading "Could not reach the API".
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from catalystiq.auth import verify_action_key
from catalystiq.main import app
from catalystiq.providers.broker import get_broker_provider

ORIGIN = "http://localhost:5173"


class _BrokenBroker:
    """Positions work, but account raises a NON-BrokerError (mirrors the real
    bug where /paper/account 500s while /paper/positions is fine)."""

    def get_account(self):
        raise ValueError("boom - not a BrokerError")

    def get_positions(self):
        return []

    def get_orders(self):
        return []


@pytest.fixture
def error_client():
    # raise_server_exceptions=False so we observe the real 500 RESPONSE the
    # browser would get (rather than the exception re-raising into the test).
    app.dependency_overrides[verify_action_key] = lambda: None
    app.dependency_overrides[get_broker_provider] = lambda: _BrokenBroker()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


def test_unhandled_error_returns_500_with_cors_headers(error_client):
    r = error_client.get("/paper/account", headers={"Origin": ORIGIN})
    # 500 (readable), NOT a thrown fetch -> the browser can parse the body.
    assert r.status_code == 500
    # The CORS header is present, so the browser doesn't block the response.
    assert r.headers.get("access-control-allow-origin") == ORIGIN
    assert r.headers.get("access-control-allow-credentials") == "true"
    # Diagnostic: names the exception type, no traceback/secret leak.
    assert "ValueError" in r.json()["detail"]


def test_working_endpoint_still_ok_with_broken_account(error_client):
    r = error_client.get("/paper/positions", headers={"Origin": ORIGIN})
    assert r.status_code == 200
    assert r.json() == []


def test_error_cors_not_echoed_for_disallowed_origin(error_client):
    r = error_client.get("/paper/account", headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 500
    assert r.headers.get("access-control-allow-origin") != "https://evil.example.com"
