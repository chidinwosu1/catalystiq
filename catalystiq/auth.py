"""Auth dependency for the action endpoints.

Accepts EITHER of two credentials:
  1. A valid session cookie (browsers - set by /auth/login, httpOnly, so the
     raw secret never lives in the browser bundle).
  2. The `action_api_key` bearer token (programmatic/CI/cron - back-compat).

Missing/invalid credentials -> 401.
"""
from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from catalystiq.config import Settings, get_settings
from catalystiq.sessions import verify_session

# auto_error=False so a missing bearer doesn't 403 before we check the cookie.
_bearer = HTTPBearer(auto_error=False)


def verify_action_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    settings: Settings = Depends(get_settings),
) -> None:
    # 1. Session cookie (browser).
    cookie = request.cookies.get(settings.session_cookie_name)
    if cookie and verify_session(cookie, settings.effective_session_secret):
        return

    # 2. Bearer token (programmatic).
    expected = settings.action_api_key.strip()
    if expected and credentials is not None and credentials.credentials == expected:
        return

    raise HTTPException(status_code=401, detail="Unauthorized.")
