"""Bronze -> Silver pipeline for the macro domain (§7, §9).

This layer serves the PERSISTED macro sources (BLS today; BEA has its own
pipeline). FRED is intentionally absent: for compliance it is ephemeral and
never persisted, living in the isolated `catalystiq.fred` package - so nothing
here fetches, stores, or reads FRED data.

    BlsProvider -> BronzeRawDocument -> SilverMacroObservation

Point-in-time is preserved end to end: an observation's vintage window
(realtime_start/realtime_end) is part of its Silver identity, so a later
revision is a new row and the originally-known value is never overwritten.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from catalystiq.db import models
from catalystiq.pipelines.ingestion import (
    finish_ingestion_run,
    latest_raw_document,
    start_ingestion_run,
    store_raw_document,
)
from catalystiq.providers.base import LicenseClassification, ProviderError, ProviderErrorCategory
from catalystiq.schemas.macro import MacroObservation

DOMAIN = "macro"
NORMALIZATION_VERSION = "1.0.0"

# Map a provider error to the terminal ingestion status (§3 allowed set).
_STATUS_BY_CATEGORY = {
    ProviderErrorCategory.RATE_LIMITED: "rate_limited",
    ProviderErrorCategory.TIMEOUT: "unavailable",
    ProviderErrorCategory.NETWORK: "unavailable",
    ProviderErrorCategory.UNAVAILABLE: "unavailable",
}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _fail_run(db, run, exc: ProviderError):
    status = _STATUS_BY_CATEGORY.get(exc.category, "failed")
    finish_ingestion_run(
        db, run, status=status, error_category=exc.category.value, error_detail=str(exc)
    )
    db.commit()


# --- Bronze -------------------------------------------------------------


def ingest_bls_series(
    provider,
    db: Session,
    series_id: str,
    start_year: int | None = None,
    end_year: int | None = None,
) -> models.BronzeIngestionRun:
    """Fetch a BLS series' observations and store them under the macro domain,
    normalized into the MacroObservation shape (with BLS-specific fields
    preserved in source_fields)."""
    run = start_ingestion_run(
        db,
        domain=DOMAIN,
        provider=provider.PROVIDER_NAME,
        adapter_version=provider.ADAPTER_VERSION,
        dataset="timeseries",
        requested_identifier=series_id,
        request_params={"series_id": series_id, "start_year": start_year, "end_year": end_year},
        license_classification=LicenseClassification.PUBLIC_DOMAIN.value,
        data_classification="end_of_day",
    )
    try:
        obs_start = dt.date(start_year, 1, 1) if start_year else None
        obs_end = dt.date(end_year, 12, 31) if end_year else None
        series = provider.get_series(series_id)
        observations = provider.get_observations(
            series_id, observation_start=obs_start, observation_end=obs_end
        )
    except ProviderError as exc:
        _fail_run(db, run, exc)
        raise

    store_raw_document(
        db, run, source_identifier=series_id, document_type="series",
        payload=series.model_dump(mode="json"),
    )
    store_raw_document(
        db, run, source_identifier=series_id, document_type="observations",
        payload={"observations": [o.model_dump(mode="json") for o in observations]},
    )
    finish_ingestion_run(db, run, status="succeeded", record_count=len(observations))
    db.commit()
    db.refresh(run)
    return run


# --- Silver -------------------------------------------------------------


def _mixin_fields(*, stable_identifier, provider, source_record_id, effective_at, run_id, warnings):
    now = _now()
    return dict(
        stable_identifier=stable_identifier,
        provider=provider,
        source_record_id=source_record_id,
        # Conservative point-in-time floor: we could not have known the value
        # before we retrieved it, so available_at defaults to the retrieval time
        # (<= retrieved_at). A source with a true earlier release time may refine
        # this later; the floor is safe against look-ahead.
        source_available_at=now,
        effective_at=effective_at,
        retrieved_at=now,
        bronze_ingestion_run_id=run_id,
        validation_status="clean_with_warnings" if warnings else "clean",
        data_quality_warnings=warnings or None,
        normalization_version=NORMALIZATION_VERSION,
    )


def build_silver_observations(db: Session, series_id: str, provider: str) -> int:
    """Upsert observations, keyed by (provider, series_id, observation_date,
    realtime_start) so vintages never collide. Returns the count upserted."""
    doc = latest_raw_document(
        db, domain=DOMAIN, source_identifier=series_id, document_type="observations"
    )
    if doc is None:
        return 0
    now = _now()
    upserted = 0
    for raw in doc.payload.get("observations", []):
        obs = MacroObservation(**raw)
        warnings = None
        if obs.value is None:
            warnings = [{"type": "missing_value", "detail": "provider reported no value"}]
        fields = _mixin_fields(
            stable_identifier=obs.series_id,
            provider=provider,
            source_record_id=f"{obs.series_id}:{obs.observation_date}:{obs.realtime_start}",
            effective_at=None,
            run_id=doc.ingestion_run_id,
            warnings=warnings,
        )
        fields.update(
            series_id=obs.series_id,
            observation_date=obs.observation_date,
            value=obs.value,
            realtime_start=obs.realtime_start,
            realtime_end=obs.realtime_end,
            units=obs.units,
            frequency=obs.frequency,
            seasonal_adjustment=obs.seasonal_adjustment,
            source_fields=obs.source_fields,
        )
        existing = (
            db.query(models.SilverMacroObservation)
            .filter_by(
                provider=provider,
                series_id=obs.series_id,
                observation_date=obs.observation_date,
                realtime_start=obs.realtime_start,
            )
            .one_or_none()
        )
        if existing is None:
            db.add(models.SilverMacroObservation(created_at=now, **fields))
        else:
            for k, v in fields.items():
                setattr(existing, k, v)
        upserted += 1
    db.commit()
    return upserted


# --- Read helpers -------------------------------------------------------


def get_silver_observations(
    db: Session, series_id: str, provider: str, as_of: dt.date | None = None
):
    """Latest-vintage observations for a series, or the point-in-time vintage
    known on `as_of` (the row whose realtime window contains it)."""
    query = db.query(models.SilverMacroObservation).filter_by(provider=provider, series_id=series_id)
    if as_of is not None:
        query = query.filter(
            models.SilverMacroObservation.realtime_start <= as_of,
            (models.SilverMacroObservation.realtime_end == None)  # noqa: E711
            | (models.SilverMacroObservation.realtime_end >= as_of),
        )
    return query.order_by(models.SilverMacroObservation.observation_date).all()
