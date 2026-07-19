"""Signed, expiring session tokens for cookie-based browser auth.

A session token proves "authenticated" for this single-admin app - it isn't
tied to a per-user identity. It's an HMAC over an expiry (no dependency on an
external session store), stored in an httpOnly cookie so it never reaches
JavaScript. See catalystiq/routers/auth.py and catalystiq/auth.py.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac

_VERSION = "v1"


def _sign(expiry: int, secret: str) -> str:
    msg = f"{_VERSION}.{expiry}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def mint_session(
    secret: str, ttl_seconds: int, now: dt.datetime | None = None
) -> tuple[str, dt.datetime]:
    """Return (cookie_value, expires_at). Raises ValueError if no secret."""
    if not secret:
        raise ValueError("session secret is not configured")
    now = now or dt.datetime.now(dt.timezone.utc)
    expires_at = now + dt.timedelta(seconds=ttl_seconds)
    expiry = int(expires_at.timestamp())
    return f"{_VERSION}.{expiry}.{_sign(expiry, secret)}", expires_at


def verify_session(value: str | None, secret: str, now: dt.datetime | None = None) -> bool:
    """True iff `value` is a well-formed, correctly-signed, unexpired token."""
    if not value or not secret:
        return False
    try:
        version, expiry_str, sig = value.split(".", 2)
        expiry = int(expiry_str)
    except (ValueError, AttributeError):
        return False
    if version != _VERSION:
        return False
    if not hmac.compare_digest(_sign(expiry, secret), sig):
        return False
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.timestamp() <= expiry
