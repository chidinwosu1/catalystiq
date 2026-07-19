"""Session-cookie auth: login sets an httpOnly cookie, protected endpoints
accept the cookie OR the bearer token (back-compat), and reject without
either. No secret is ever required in the browser bundle."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from catalystiq.config import Settings, get_settings
from catalystiq.main import app
from catalystiq.sessions import mint_session, verify_session

_SETTINGS = Settings(
    action_api_key="bearer-key",
    app_password="s3cret-pw",
    session_secret="signing-secret",
    session_cookie_secure=False,  # TestClient talks HTTP
)


@pytest.fixture
def auth_client():
    # NOTE: unlike the shared `client` fixture, this does NOT override
    # verify_action_key - it exercises the real auth dependency.
    app.dependency_overrides[get_settings] = lambda: _SETTINGS
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_settings, None)


# --- unit: session token ------------------------------------------------

def test_session_roundtrip_and_expiry():
    token, _ = mint_session("secret", 3600)
    assert verify_session(token, "secret") is True
    assert verify_session(token, "wrong-secret") is False
    assert verify_session("garbage", "secret") is False
    # Expired.
    past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)
    old, _ = mint_session("secret", 1, now=past)
    assert verify_session(old, "secret") is False


# --- endpoint behavior --------------------------------------------------

def test_protected_endpoint_rejects_without_auth(auth_client):
    # Fresh client, no cookie/bearer.
    assert auth_client.get("/data-sources").status_code == 401


def test_login_wrong_password_rejected(auth_client):
    r = auth_client.post("/auth/login", json={"password": "nope"})
    assert r.status_code == 401
    # No session established.
    assert auth_client.get("/data-sources").status_code == 401


def test_login_sets_cookie_and_authorizes(auth_client):
    r = auth_client.post("/auth/login", json={"password": "s3cret-pw"})
    assert r.status_code == 200
    assert r.json()["authenticated"] is True
    # httpOnly session cookie set.
    assert "ciq_session" in auth_client.cookies
    # Now the protected endpoint works via the cookie (no bearer).
    assert auth_client.get("/data-sources").status_code == 200
    # Session endpoint reflects it.
    assert auth_client.get("/auth/session").json()["authenticated"] is True


def test_bearer_token_still_works(auth_client):
    # Back-compat: programmatic callers can still use the bearer token.
    r = auth_client.get("/data-sources", headers={"Authorization": "Bearer bearer-key"})
    assert r.status_code == 200
    r2 = auth_client.get("/data-sources", headers={"Authorization": "Bearer wrong"})
    assert r2.status_code == 401


def test_logout_clears_session(auth_client):
    auth_client.post("/auth/login", json={"password": "s3cret-pw"})
    assert auth_client.get("/data-sources").status_code == 200
    auth_client.post("/auth/logout")
    assert auth_client.get("/data-sources").status_code == 401
    assert auth_client.get("/auth/session").json()["authenticated"] is False


def test_login_password_defaults_to_action_api_key():
    # With no explicit app_password/session_secret, action_api_key is used
    # for both - a single configured secret works.
    settings = Settings(action_api_key="only-key", session_cookie_secure=False)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as c:
            assert c.post("/auth/login", json={"password": "only-key"}).status_code == 200
            assert c.get("/data-sources").status_code == 200
    finally:
        app.dependency_overrides.pop(get_settings, None)
