"""Session-cookie auth: login sets an httpOnly cookie, protected endpoints
accept the cookie OR the bearer token (back-compat), and reject without
either. No secret is ever required in the browser bundle."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from catalystiq.config import (
    MIN_APP_PASSWORD_LENGTH,
    MIN_SESSION_SECRET_LENGTH,
    ConfigurationError,
    Settings,
    get_settings,
    validate_auth_config,
    validate_settings,
)
from catalystiq.main import app
from catalystiq.sessions import mint_session, verify_session

# Valid production secrets for the "happy path" checks below.
_STRONG_PW = "a-strong-workspace-password"
_STRONG_SECRET = "x" * (MIN_SESSION_SECRET_LENGTH + 8)


def _prod(**overrides) -> Settings:
    base = dict(
        environment="production",
        app_password=_STRONG_PW,
        session_secret=_STRONG_SECRET,
        session_cookie_secure=True,
    )
    base.update(overrides)
    return Settings(**base)

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


def test_login_password_defaults_to_action_api_key_in_dev():
    # DEV ONLY: with no explicit app_password/session_secret, action_api_key
    # is used for both - a single configured secret works locally.
    settings = Settings(action_api_key="only-key", session_cookie_secure=False)
    assert settings.is_production is False
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as c:
            assert c.post("/auth/login", json={"password": "only-key"}).status_code == 200
            assert c.get("/data-sources").status_code == 200
    finally:
        app.dependency_overrides.pop(get_settings, None)


# --- production auth hardening (§4/§5) ----------------------------------

def test_production_requires_explicit_password_and_secret():
    problems = validate_auth_config(Settings(environment="production"))
    joined = " ".join(problems)
    assert "APP_PASSWORD" in joined
    assert "SESSION_SECRET" in joined
    # And validate_settings raises (startup would fail).
    with pytest.raises(ConfigurationError):
        validate_settings(Settings(environment="production"))


def test_production_rejects_fallback_to_action_api_key():
    # Both blank -> would fall back to action_api_key in dev; production must
    # reject that (they're blank, so "must be set explicitly").
    problems = validate_auth_config(
        Settings(environment="production", action_api_key="some-bearer-key")
    )
    assert any("APP_PASSWORD" in p and "explicitly" in p for p in problems)
    assert any("SESSION_SECRET" in p and "explicitly" in p for p in problems)
    # Explicitly setting them EQUAL to action_api_key is also rejected.
    reused = validate_auth_config(
        _prod(action_api_key=_STRONG_PW, app_password=_STRONG_PW)
    )
    assert any("APP_PASSWORD must not" in p for p in reused)


def test_production_rejects_identical_password_and_secret():
    same = "identical-value-" + "y" * MIN_SESSION_SECRET_LENGTH
    problems = validate_auth_config(_prod(app_password=same, session_secret=same))
    assert any("different values" in p for p in problems)


def test_production_rejects_blank_values():
    problems = validate_auth_config(_prod(app_password="   ", session_secret="   "))
    assert any("APP_PASSWORD must be set" in p for p in problems)
    assert any("SESSION_SECRET must be set" in p for p in problems)


def test_production_rejects_short_secrets():
    problems = validate_auth_config(_prod(app_password="short", session_secret="also-short"))
    assert any(str(MIN_APP_PASSWORD_LENGTH) in p for p in problems)
    assert any(str(MIN_SESSION_SECRET_LENGTH) in p for p in problems)


def test_production_requires_secure_cookie():
    problems = validate_auth_config(_prod(session_cookie_secure=False))
    assert any("SESSION_COOKIE_SECURE" in p for p in problems)


def test_production_valid_config_passes():
    assert validate_auth_config(_prod()) == []
    # Full validate_settings (auth + data sources) also passes.
    validate_settings(_prod())


def test_validation_messages_never_contain_secret_values():
    # A too-short but non-blank secret value must NOT appear in the error.
    pw_value = "shortpw"
    secret_value = "shortsecret"
    problems = validate_auth_config(
        _prod(app_password=pw_value, session_secret=secret_value)
    )
    blob = " ".join(problems)
    assert pw_value not in blob
    assert secret_value not in blob


# --- cookie security + secret non-disclosure (§3, §7) -------------------

def test_login_cookie_is_httponly_secure_samesite():
    settings = Settings(
        app_password="pw-1234567890", session_secret="s" * 40, session_cookie_secure=True
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as c:
            r = c.post("/auth/login", json={"password": "pw-1234567890"})
            assert r.status_code == 200
            set_cookie = r.headers.get("set-cookie", "").lower()
            assert "httponly" in set_cookie
            assert "secure" in set_cookie
            assert "samesite=lax" in set_cookie
            assert "path=/" in set_cookie
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_secrets_never_disclosed_by_endpoints(auth_client):
    login = auth_client.post("/auth/login", json={"password": "s3cret-pw"})
    session = auth_client.get("/auth/session")
    set_cookie = login.headers.get("set-cookie", "")
    for secret in ("s3cret-pw", "signing-secret"):
        assert secret not in login.text
        assert secret not in session.text
        # The cookie carries a signed token, never the raw secret/password.
        assert secret not in set_cookie
