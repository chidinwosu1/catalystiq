"""Data-source administrative/health endpoints (§18).

Read-only visibility into every registered source: whether it's enabled and
configured, when it last ingested successfully, its last failure category,
and data freshness. Never returns secrets, request headers, or broker
details - only setting *names* for anything missing.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from catalystiq.auth import verify_action_key
from catalystiq.config import Settings, get_settings
from catalystiq.db import models
from catalystiq.db.base import get_db
from catalystiq.providers import registry

router = APIRouter(
    prefix="/data-sources",
    tags=["data-sources"],
    dependencies=[Depends(verify_action_key)],
)

# Bronze runs for the price-bar domain record the provider by class name;
# map a source name to any legacy aliases so health can find its runs.
_PROVIDER_ALIASES = {"yahoo": ("yahoo", "YahooFinanceProvider")}

_FAILED_STATUSES = ("failed", "unavailable", "rate_limited")


def _provider_keys(name: str) -> tuple[str, ...]:
    return _PROVIDER_ALIASES.get(name, (name,))


def _iso(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=dt.timezone.utc).isoformat()


def _health_for(source: registry.SourceDescriptor, settings: Settings, db: Session) -> dict:
    keys = _provider_keys(source.name)
    missing = registry.missing_settings(source.name, settings)

    last_success = (
        db.query(func.max(models.BronzeIngestionRun.completed_at))
        .filter(
            models.BronzeIngestionRun.provider.in_(keys),
            models.BronzeIngestionRun.status == "succeeded",
        )
        .scalar()
    )
    last_failure = (
        db.query(models.BronzeIngestionRun)
        .filter(
            models.BronzeIngestionRun.provider.in_(keys),
            models.BronzeIngestionRun.status.in_(_FAILED_STATUSES),
        )
        .order_by(models.BronzeIngestionRun.completed_at.desc())
        .first()
    )

    return {
        "name": source.name,
        "domain": source.domain.value,
        "implemented": source.implemented,
        "enabled": registry.is_source_enabled(source.name, settings),
        "configured": not missing,
        "missing_settings": missing,  # names only, never values
        "requires_api_key": source.requires_api_key,
        "license": source.license.value,
        "last_successful_ingestion_at": _iso(last_success),
        "last_failure_category": last_failure.error_category if last_failure else None,
        "last_failure_at": _iso(last_failure.completed_at) if last_failure else None,
        # Circuit-breaker state is per-process/in-memory in the transport
        # layer and not persisted, so it isn't reported here.
        "circuit_breaker": "not_tracked",
        "data_freshness_at": _iso(last_success),
    }


@router.get("")
def list_data_sources(settings: Settings = Depends(get_settings)):
    """Every registered source with its enable/config/implementation state."""
    return [
        {
            "name": s.name,
            "domain": s.domain.value,
            "implemented": s.implemented,
            "enabled": registry.is_source_enabled(s.name, settings),
            "configured": registry.is_source_configured(s.name, settings),
            "requires_api_key": s.requires_api_key,
            "license": s.license.value,
        }
        for s in registry.SOURCE_REGISTRY
    ]


@router.get("/health")
def all_health(settings: Settings = Depends(get_settings), db: Session = Depends(get_db)):
    return [_health_for(s, settings, db) for s in registry.SOURCE_REGISTRY]


@router.get("/{provider}/health")
def provider_health(
    provider: str, settings: Settings = Depends(get_settings), db: Session = Depends(get_db)
):
    source = registry.get_source(provider)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Unknown data source {provider!r}.")
    return _health_for(source, settings, db)
