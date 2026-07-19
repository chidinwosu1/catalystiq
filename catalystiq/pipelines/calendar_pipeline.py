"""Bronze -> Silver pipeline for the market-calendar domain (§10).

    NyseCalendarProvider -> BronzeRawDocument -> SilverMarketSession

ingest_bronze_sessions() fetches the schedule and stores it as one raw
document (append-only). build_silver_sessions() reads that document, runs
structural validation, and upserts normalized SilverMarketSession rows
(idempotent on exchange+session_date). Downstream code reads Silver only.
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
from catalystiq.providers.base import LicenseClassification
from catalystiq.providers.calendars import CalendarError, MarketCalendarProvider
from catalystiq.schemas.calendar import MarketSession

DOMAIN = "market_calendar"
DOCUMENT_TYPE = "schedule"
NORMALIZATION_VERSION = "1.0.0"

# Structural sanity bounds for a session (validation, not fabrication): a
# regular US equity session closes between 13:00 (half-day) and 16:00 ET.
_MIN_CLOSE_HOUR_ET = 13
_REGULAR_CLOSE_HOUR_ET = 16
_NY_TZ = "America/New_York"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _to_naive_utc(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return value


def ingest_bronze_sessions(
    provider: MarketCalendarProvider,
    db: Session,
    start: dt.date,
    end: dt.date,
) -> models.BronzeIngestionRun:
    """Fetch the exchange schedule and persist it as a raw document under a
    new ingestion run. Never touches Silver."""
    exchange = getattr(provider, "EXCHANGE", "NYSE")
    run = start_ingestion_run(
        db,
        domain=DOMAIN,
        provider=getattr(provider, "PROVIDER_NAME", type(provider).__name__),
        adapter_version=getattr(provider, "ADAPTER_VERSION", None),
        dataset=DOCUMENT_TYPE,
        requested_identifier=exchange,
        request_params={"start": start.isoformat(), "end": end.isoformat()},
        license_classification=LicenseClassification.FREE_ATTRIBUTION.value,
        data_classification="reference",
    )
    try:
        sessions = provider.get_sessions(start, end)
    except CalendarError as exc:
        finish_ingestion_run(
            db, run, status="failed", error_category="unavailable", error_detail=str(exc)
        )
        db.commit()
        raise

    payload = {
        "exchange": exchange,
        "sessions": [s.model_dump(mode="json") for s in sessions],
    }
    store_raw_document(
        db,
        run,
        source_identifier=exchange,
        document_type=DOCUMENT_TYPE,
        payload=payload,
    )
    finish_ingestion_run(db, run, status="succeeded", record_count=len(sessions))
    db.commit()
    db.refresh(run)
    return run


def _validate_session(session: MarketSession) -> list[dict]:
    """Structural invariants; returns data-quality warnings (never raises).
    A full cross-check against the officially published NYSE calendar is a
    documented follow-up - these catch gross package errors, not holiday
    naming."""
    warnings: list[dict] = []
    if session.open_at and session.close_at and session.open_at >= session.close_at:
        warnings.append({"type": "invalid_session_bounds", "detail": "open_at >= close_at"})
    if session.session_date.weekday() >= 5:
        warnings.append({"type": "weekend_session", "detail": "session on a weekend"})
    if session.close_at is not None:
        import zoneinfo

        close_et = session.close_at.astimezone(zoneinfo.ZoneInfo(_NY_TZ))
        if not (_MIN_CLOSE_HOUR_ET <= close_et.hour <= _REGULAR_CLOSE_HOUR_ET):
            warnings.append(
                {"type": "implausible_close_time", "detail": f"close {close_et.time()} ET"}
            )
    return warnings


def build_silver_sessions(db: Session, exchange: str = "NYSE") -> int:
    """Normalize the latest raw schedule document into SilverMarketSession
    rows, upserting on (exchange, session_date). Returns the number
    upserted. Idempotent - reprocessing the same document changes nothing."""
    doc = latest_raw_document(
        db, domain=DOMAIN, source_identifier=exchange, document_type=DOCUMENT_TYPE
    )
    if doc is None:
        return 0

    now = _now()
    upserted = 0
    for raw in doc.payload.get("sessions", []):
        session = MarketSession(**raw)
        warnings = _validate_session(session)
        status = "clean_with_warnings" if warnings else "clean"

        existing = (
            db.query(models.SilverMarketSession)
            .filter_by(exchange=session.exchange, session_date=session.session_date)
            .one_or_none()
        )
        fields = dict(
            stable_identifier=f"{session.exchange}:{session.session_date.isoformat()}",
            provider=session.source,
            source_record_id=session.session_date.isoformat(),
            source_available_at=_to_naive_utc(session.retrieved_at) or now,  # PIT floor
            effective_at=_to_naive_utc(session.open_at),
            retrieved_at=_to_naive_utc(session.retrieved_at) or now,
            bronze_ingestion_run_id=doc.ingestion_run_id,
            validation_status=status,
            data_quality_warnings=warnings or None,
            normalization_version=NORMALIZATION_VERSION,
            open_at=_to_naive_utc(session.open_at),
            close_at=_to_naive_utc(session.close_at),
            timezone=session.timezone,
            early_close=session.early_close,
            holiday_name=session.holiday_name,
            calendar_version=session.calendar_version,
        )
        if existing is None:
            db.add(
                models.SilverMarketSession(
                    exchange=session.exchange,
                    session_date=session.session_date,
                    created_at=now,
                    **fields,
                )
            )
        else:
            for key, value in fields.items():
                setattr(existing, key, value)
        upserted += 1

    db.commit()
    return upserted


def ensure_sessions(
    provider: MarketCalendarProvider,
    db: Session,
    start: dt.date,
    end: dt.date,
    exchange: str = "NYSE",
) -> int:
    """Convenience: ingest the schedule for [start, end] then build Silver.
    Used by the calendar endpoint and startup/periodic refresh."""
    ingest_bronze_sessions(provider, db, start, end)
    return build_silver_sessions(db, exchange=exchange)


def get_silver_sessions(
    db: Session,
    exchange: str,
    start: dt.date | None = None,
    end: dt.date | None = None,
) -> list[models.SilverMarketSession]:
    query = db.query(models.SilverMarketSession).filter_by(exchange=exchange)
    if start is not None:
        query = query.filter(models.SilverMarketSession.session_date >= start)
    if end is not None:
        query = query.filter(models.SilverMarketSession.session_date <= end)
    return query.order_by(models.SilverMarketSession.session_date).all()
