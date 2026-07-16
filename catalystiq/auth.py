"""Shared bearer-token auth dependency for action endpoints."""
from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from catalystiq.config import get_settings

security = HTTPBearer()


def verify_action_key(
    credentials: HTTPAuthorizationCredentials = Security(security),
) -> None:
    expected_key = get_settings().action_api_key.strip()

    if not expected_key:
        raise HTTPException(
            status_code=500,
            detail="ACTION_API_KEY is not configured.",
        )

    if credentials.credentials != expected_key:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized.",
        )
