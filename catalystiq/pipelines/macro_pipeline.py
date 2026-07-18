"""Bronze -> Silver pipeline for the macro domain (§7, §9).

    FredProvider -> BronzeRawDocument -> SilverMacro{Series,Observation}
                                      -> SilverEconomicRelease

The adapter already normalizes FRED's response into provider-agnostic
MacroSeries/MacroObservation objects, so (as with the price-bar Bronze) the
stored raw document is "source-aligned" - what the adapter returned - not the
byte-for-byte FRED JSON. Silver builds read only from those documents.

Point-in-time is preserved end to end: an observation's vintage window
(realtime_start/realtime_end) is carried into Silver and is part of its
identity, so a later revision is a new row and the originally-known value is
never overwritten.
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
from catalystiq.providers.macro import FredProvider
from catalystiq.schemas.macro import EconomicRelease, MacroObservation, MacroSeries

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


def _naive(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value.astimezone(dt.timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def _fail_run(db, run, exc: ProviderError):
    status = _STATUS_BY_CATEGORY.get(exc.category, "failed")
    finish_ingestion_run(
        db, run, status=status, error_category=exc.category.value, error_detail=str(exc)
    )
    db.commit()


# --- Bronze -------------------------------------------------------------


def ingest_series(
    provider: FredProvider,
    db: Session,
    series_id: str,
    observation_start: dt.date | None = None,
    as_of: dt.date | None = None,
) -> models.BronzeIngestionRun:
    """Fetch series metadata + observations and store them as raw documents.
    `as_of` requests the ALFRED vintage known on that historical date."""
    run = start_ingestion_run(
        db,
        domain=DOMAIN,
        provider=provider.PROVIDER_NAME,
        adapter_version=provider.ADAPTER_VERSION,
        dataset="series/observations",
        requested_identifier=series_id,
        request_params={
            "series_id": series_id,
            "observation_start": observation_start.isoformat() if observation_start else None,
            "as_of": as_of.isoformat() if as_of else None,
        },
        license_classification=LicenseClassification.PUBLIC_DOMAIN.value,
        data_classification="revised" if as_of else "end_of_day",
    )
    try:
        series = provider.get_series(series_id)
        observations = provider.get_observations(
            series_id, observation_start=observation_start, as_of=as_of
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


def ingest_bls_series(
    provider,
    db: Session,
    series_id: str,
    start_year: int | None = None,
    end_year: int | None = None,
) -> models.BronzeIngestionRun:
    """Fetch a BLS series' observations and store them under the macro domain,
    normalized into the same MacroObservation shape as FRED (with BLS-specific
    fields preserved in source_fields)."""
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


def ingest_releases(provider: FredProvider, db: Session) -> models.BronzeIngestionRun:
    run = start_ingestion_run(
        db,
        domain=DOMAIN,
        provider=provider.PROVIDER_NAME,
        adapter_version=provider.ADAPTER_VERSION,
        dataset="releases",
        requested_identifier="releases",
        license_classification=LicenseClassification.PUBLIC_DOMAIN.value,
        data_classification="reference",
    )
    try:
        releases = provider.get_releases()
    except ProviderError as exc:
        _fail_run(db, run, exc)
        raise

    store_raw_document(
        db, run, source_identifier="releases", document_type="releases",
        payload={"releases": [r.model_dump(mode="json") for r in releases]},
    )
    finish_ingestion_run(db, run, status="succeeded", record_count=len(releases))
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
        source_available_at=None,
        effective_at=effective_at,
        retrieved_at=now,
        bronze_ingestion_run_id=run_id,
        validation_status="clean_with_warnings" if warnings else "clean",
        data_quality_warnings=warnings or None,
        normalization_version=NORMALIZATION_VERSION,
    )


def build_silver_series(db: Session, series_id: str, provider: str = "fred") -> bool:
    doc = latest_raw_document(db, domain=DOMAIN, source_identifier=series_id, document_type="series")
    if doc is None:
        return False
    series = MacroSeries(**doc.payload)
    now = _now()
    fields = _mixin_fields(
        stable_identifier=series.series_id,
        provider=provider,
        source_record_id=series.series_id,
        effective_at=None,
        run_id=doc.ingestion_run_id,
        warnings=None,
    )
    fields.update(
        series_id=series.series_id,
        title=series.title,
        frequency=series.frequency,
        units=series.units,
        seasonal_adjustment=series.seasonal_adjustment,
        observation_start=series.observation_start,
        observation_end=series.observation_end,
    )
    existing = (
        db.query(models.SilverMacroSeries)
        .filter_by(provider=provider, series_id=series.series_id)
        .one_or_none()
    )
    if existing is None:
        db.add(models.SilverMacroSeries(created_at=now, **fields))
    else:
        for k, v in fields.items():
            setattr(existing, k, v)
    db.commit()
    return True


def build_silver_observations(db: Session, series_id: str, provider: str = "fred") -> int:
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


def build_silver_releases(db: Session, provider: str = "fred") -> int:
    doc = latest_raw_document(
        db, domain=DOMAIN, source_identifier="releases", document_type="releases"
    )
    if doc is None:
        return 0
    now = _now()
    upserted = 0
    for raw in doc.payload.get("releases", []):
        rel = EconomicRelease(**raw)
        fields = _mixin_fields(
            stable_identifier=rel.release_id,
            provider=provider,
            source_record_id=rel.release_id,
            effective_at=None,
            run_id=doc.ingestion_run_id,
            warnings=None,
        )
        fields.update(
            release_id=rel.release_id,
            name=rel.name,
            scheduled_date=rel.scheduled_date,
            actual_published_at=_naive(rel.actual_published_at),
            press_release=rel.press_release,
            link=rel.link,
        )
        existing = (
            db.query(models.SilverEconomicRelease)
            .filter_by(provider=provider, release_id=rel.release_id, scheduled_date=rel.scheduled_date)
            .one_or_none()
        )
        if existing is None:
            db.add(models.SilverEconomicRelease(created_at=now, **fields))
        else:
            for k, v in fields.items():
                setattr(existing, k, v)
        upserted += 1
    db.commit()
    return upserted


# --- Read helpers -------------------------------------------------------


def get_silver_series(db: Session, series_id: str, provider: str = "fred"):
    return (
        db.query(models.SilverMacroSeries)
        .filter_by(provider=provider, series_id=series_id)
        .one_or_none()
    )


def get_silver_observations(
    db: Session, series_id: str, provider: str = "fred", as_of: dt.date | None = None
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


def get_silver_releases(db: Session, provider: str = "fred"):
    return (
        db.query(models.SilverEconomicRelease)
        .filter_by(provider=provider)
        .order_by(models.SilverEconomicRelease.release_id)
        .all()
    )
