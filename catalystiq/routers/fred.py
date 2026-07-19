"""Rule-Based Macroeconomic Context endpoints (isolated FRED surface).

Deliberately minimal and self-contained: no database dependency, no pipeline
imports. Every response is served with `Cache-Control: no-store` so FRED
values are never written to a browser/proxy cache (compliance requirement #3),
and every payload carries the required FRED attribution notice.

If FRED is disabled or unconfigured these endpoints still return 200 with
`available: False` - the panel is optional and its absence must never break
the app (kill switch, requirement #8).
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from catalystiq.auth import verify_action_key
from catalystiq.config import Settings, get_settings
from catalystiq.fred import service
from catalystiq.fred.allowlist import SeriesBlocked, SeriesNotAllowed

router = APIRouter(
    prefix="/fred",
    tags=["fred"],
    dependencies=[Depends(verify_action_key)],
)


def _no_store(response: Response) -> None:
    """Forbid any storage of FRED responses by browsers/proxies."""
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


@router.get("/context")
def get_macro_context(
    response: Response,
    as_of: dt.date | None = Query(
        default=None,
        description="Optional ALFRED point-in-time date (vintage known then).",
    ),
    settings: Settings = Depends(get_settings),
):
    """The Rule-Based Macroeconomic Context panel: allowlisted, public-domain
    indicators rendered ephemerally with per-indicator attribution."""
    _no_store(response)
    return service.build_context(settings, as_of=as_of)


@router.get("/series")
def list_series(response: Response):
    """The reviewed allowlist (approved + blocked) with attribution/copyright
    status. Metadata only - no values fetched."""
    _no_store(response)
    return service.list_allowlist()


@router.get("/series/{series_id}")
def get_series(
    series_id: str,
    response: Response,
    as_of: dt.date | None = Query(default=None),
    settings: Settings = Depends(get_settings),
):
    """Ephemeral observations for one allowlisted series. A series that is not
    on the allowlist (404) or is blocked/copyrighted (403) is never fetched."""
    _no_store(response)
    try:
        return service.get_series_context(settings, series_id, as_of=as_of)
    except SeriesNotAllowed as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SeriesBlocked as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
