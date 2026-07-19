"""Session-cookie auth endpoints.

The browser exchanges a password for an httpOnly session cookie here, so the
raw secret is never baked into the frontend bundle. These endpoints are
public (they establish auth); everything else stays behind verify_action_key.

Brute-force note: login uses a constant-time comparison but no rate limiter -
add one (or front the app with a WAF/proxy) before exposing this publicly.
"""
from __future__ import annotations

import datetime as dt
import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from catalystiq.config import Settings, get_settings
from catalystiq.sessions import mint_session, verify_session

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str


class SessionStatus(BaseModel):
    authenticated: bool
    expires_at: dt.datetime | None = None


def _set_session_cookie(response: Response, settings: Settings, value: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=value,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        max_age=settings.session_ttl_seconds,
        path="/",
    )


@router.post("/login", response_model=SessionStatus)
def login(
    payload: LoginRequest,
    response: Response,
    settings: Settings = Depends(get_settings),
):
    expected = settings.effective_app_password
    if not expected or not settings.effective_session_secret:
        raise HTTPException(
            status_code=500,
            detail="Authentication is not configured on the server (set ACTION_API_KEY or "
            "APP_PASSWORD + SESSION_SECRET).",
        )
    if not hmac.compare_digest(payload.password, expected):
        raise HTTPException(status_code=401, detail="Invalid password.")

    token, expires_at = mint_session(
        settings.effective_session_secret, settings.session_ttl_seconds
    )
    _set_session_cookie(response, settings, token)
    return SessionStatus(authenticated=True, expires_at=expires_at)


@router.post("/logout")
def logout(response: Response, settings: Settings = Depends(get_settings)):
    response.delete_cookie(key=settings.session_cookie_name, path="/")
    return {"ok": True}


@router.get("/session", response_model=SessionStatus)
def session(request: Request, settings: Settings = Depends(get_settings)):
    cookie = request.cookies.get(settings.session_cookie_name)
    return SessionStatus(
        authenticated=bool(cookie and verify_session(cookie, settings.effective_session_secret))
    )
