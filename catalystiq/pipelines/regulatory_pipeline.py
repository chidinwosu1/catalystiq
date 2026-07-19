"""Bronze -> Silver pipeline for the regulatory domain (§11, §12).

    FinraProvider        -> BronzeRawDocument -> SilverShortSaleVolume
                                              -> SilverShortInterest
    NasdaqTraderProvider -> BronzeRawDocument -> SilverSecurityMaster

Short interest and daily short-sale volume are kept as separate datasets and
Silver tables. `file_version` is part of each Silver identity, so a corrected
FINRA file is preserved alongside the original rather than overwriting it.
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
from catalystiq.schemas.regulatory import (
    SecurityMasterEntry,
    ShortInterest,
    ShortSaleVolume,
)

SECURITY_MASTER_DOMAIN = "reference"

DOMAIN = "regulatory"
NORMALIZATION_VERSION = "1.0.0"

_STATUS_BY_CATEGORY = {
    ProviderErrorCategory.RATE_LIMITED: "rate_limited",
    ProviderErrorCategory.TIMEOUT: "unavailable",
    ProviderErrorCategory.NETWORK: "unavailable",
    ProviderErrorCategory.UNAVAILABLE: "unavailable",
}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _fail(db, run, exc: ProviderError):
    finish_ingestion_run(
        db, run, status=_STATUS_BY_CATEGORY.get(exc.category, "failed"),
        error_category=exc.category.value, error_detail=str(exc),
    )
    db.commit()


def _mixin(*, stable_identifier, provider, source_record_id, effective_at, run_id):
    now = _now()
    return dict(
        stable_identifier=stable_identifier, provider=provider,
        source_record_id=source_record_id, source_available_at=now,  # PIT floor
        effective_at=effective_at, retrieved_at=now, bronze_ingestion_run_id=run_id,
        validation_status="clean", data_quality_warnings=None,
        normalization_version=NORMALIZATION_VERSION,
    )


def _upsert(db, model, key: dict, fields: dict):
    existing = db.query(model).filter_by(**key).one_or_none()
    if existing is None:
        db.add(model(created_at=_now(), **fields))
    else:
        for k, v in fields.items():
            setattr(existing, k, v)


# --- FINRA daily short-sale volume --------------------------------------


def ingest_short_sale_volume(
    provider, db: Session, trade_date: dt.date, file_version: str = "original"
) -> models.BronzeIngestionRun:
    run = start_ingestion_run(
        db, domain=DOMAIN, provider=provider.PROVIDER_NAME,
        adapter_version=provider.ADAPTER_VERSION, dataset="short_sale_volume",
        requested_identifier=trade_date.isoformat(),
        request_params={"trade_date": trade_date.isoformat(), "file_version": file_version},
        license_classification=LicenseClassification.FREE_ATTRIBUTION.value,
        data_classification="end_of_day",
    )
    try:
        rows = provider.get_short_sale_volume(trade_date, file_version=file_version)
    except ProviderError as exc:
        _fail(db, run, exc)
        raise
    store_raw_document(
        db, run, source_identifier=f"shvol:{trade_date.isoformat()}:{file_version}",
        document_type="short_sale_volume",
        payload={"rows": [r.model_dump(mode="json") for r in rows]},
    )
    finish_ingestion_run(db, run, status="succeeded", record_count=len(rows))
    db.commit()
    db.refresh(run)
    return run


def build_silver_short_sale_volume(
    db: Session, trade_date: dt.date, file_version: str = "original", provider: str = "finra"
) -> int:
    doc = latest_raw_document(
        db, domain=DOMAIN,
        source_identifier=f"shvol:{trade_date.isoformat()}:{file_version}",
        document_type="short_sale_volume",
    )
    if doc is None:
        return 0
    n = 0
    for raw in doc.payload.get("rows", []):
        v = ShortSaleVolume(**raw)
        fields = _mixin(
            stable_identifier=v.symbol, provider=provider,
            source_record_id=f"{v.symbol}:{v.trade_date}:{v.reporting_facility}",
            effective_at=None, run_id=doc.ingestion_run_id,
        )
        fields.update(
            symbol=v.symbol, trade_date=v.trade_date, short_volume=v.short_volume,
            short_exempt_volume=v.short_exempt_volume, total_volume=v.total_volume,
            reporting_facility=v.reporting_facility, file_version=v.file_version,
        )
        _upsert(
            db, models.SilverShortSaleVolume,
            dict(provider=provider, symbol=v.symbol, trade_date=v.trade_date,
                 reporting_facility=v.reporting_facility, file_version=v.file_version),
            fields,
        )
        n += 1
    db.commit()
    return n


# --- FINRA short interest -----------------------------------------------


def ingest_short_interest_text(
    provider, db: Session, text: str, settlement_hint: str = "", file_version: str = "original"
) -> models.BronzeIngestionRun:
    """Ingest a short-interest file body (already fetched/provided). Kept
    text-based because the public file's exact URL varies; the parser is
    header-driven."""
    run = start_ingestion_run(
        db, domain=DOMAIN, provider=provider.PROVIDER_NAME,
        adapter_version=provider.ADAPTER_VERSION, dataset="short_interest",
        requested_identifier=settlement_hint or "short_interest",
        request_params={"settlement_hint": settlement_hint, "file_version": file_version},
        license_classification=LicenseClassification.FREE_ATTRIBUTION.value,
        data_classification="end_of_day",
    )
    rows = provider.parse_short_interest(text, file_version=file_version)
    store_raw_document(
        db, run, source_identifier=f"si:{settlement_hint or 'latest'}:{file_version}",
        document_type="short_interest",
        payload={"rows": [r.model_dump(mode="json") for r in rows]},
    )
    finish_ingestion_run(db, run, status="succeeded", record_count=len(rows))
    db.commit()
    db.refresh(run)
    return run


def build_silver_short_interest_from_run(
    db: Session, run_id: int, provider: str = "finra"
) -> int:
    doc = (
        db.query(models.BronzeRawDocument)
        .filter_by(ingestion_run_id=run_id, document_type="short_interest")
        .first()
    )
    if doc is None:
        return 0
    n = 0
    for raw in doc.payload.get("rows", []):
        v = ShortInterest(**raw)
        fields = _mixin(
            stable_identifier=v.symbol, provider=provider,
            source_record_id=f"{v.symbol}:{v.settlement_date}",
            effective_at=None, run_id=doc.ingestion_run_id,
        )
        fields.update(
            symbol=v.symbol, settlement_date=v.settlement_date,
            publication_date=v.publication_date,
            short_interest_quantity=v.short_interest_quantity,
            previous_short_interest_quantity=v.previous_short_interest_quantity,
            average_daily_volume=v.average_daily_volume, days_to_cover=v.days_to_cover,
            file_version=v.file_version,
        )
        _upsert(
            db, models.SilverShortInterest,
            dict(provider=provider, symbol=v.symbol, settlement_date=v.settlement_date,
                 file_version=v.file_version),
            fields,
        )
        n += 1
    db.commit()
    return n


# --- Nasdaq Trader security master --------------------------------------


def ingest_security_master(provider, db: Session) -> models.BronzeIngestionRun:
    run = start_ingestion_run(
        db, domain=SECURITY_MASTER_DOMAIN, provider=provider.PROVIDER_NAME,
        adapter_version=provider.ADAPTER_VERSION, dataset="symbol_directory",
        requested_identifier="symbol_directory",
        license_classification=LicenseClassification.FREE_ATTRIBUTION.value,
        data_classification="reference",
    )
    try:
        entries = provider.get_securities()
    except ProviderError as exc:
        _fail(db, run, exc)
        raise
    store_raw_document(
        db, run, source_identifier="symbol_directory", document_type="symbol_directory",
        payload={"securities": [e.model_dump(mode="json") for e in entries]},
    )
    finish_ingestion_run(db, run, status="succeeded", record_count=len(entries))
    db.commit()
    db.refresh(run)
    return run


def build_silver_security_master(db: Session, provider: str = "nasdaq_trader") -> int:
    doc = latest_raw_document(
        db, domain=SECURITY_MASTER_DOMAIN, source_identifier="symbol_directory",
        document_type="symbol_directory",
    )
    if doc is None:
        return 0
    n = 0
    for raw in doc.payload.get("securities", []):
        e = SecurityMasterEntry(**raw)
        fields = _mixin(
            stable_identifier=e.internal_security_id, provider=provider,
            source_record_id=e.internal_security_id, effective_at=None,
            run_id=doc.ingestion_run_id,
        )
        fields.update(
            internal_security_id=e.internal_security_id, symbol=e.symbol, name=e.name,
            exchange=e.exchange, listing_market=e.listing_market, etf=e.etf,
            test_issue=e.test_issue, is_active=e.is_active,
        )
        _upsert(
            db, models.SilverSecurityMaster,
            dict(provider=provider, internal_security_id=e.internal_security_id), fields,
        )
        n += 1
    db.commit()
    return n


def get_security_master(db: Session, symbol: str, provider: str = "nasdaq_trader"):
    return (
        db.query(models.SilverSecurityMaster)
        .filter_by(provider=provider, symbol=symbol.upper())
        .all()
    )


# --- Read helpers -------------------------------------------------------


def get_short_interest(db: Session, symbol: str, provider: str = "finra"):
    return (
        db.query(models.SilverShortInterest)
        .filter_by(provider=provider, symbol=symbol.upper())
        .order_by(models.SilverShortInterest.settlement_date.desc())
        .all()
    )


def get_short_sale_volume(db: Session, symbol: str, provider: str = "finra"):
    return (
        db.query(models.SilverShortSaleVolume)
        .filter_by(provider=provider, symbol=symbol.upper())
        .order_by(models.SilverShortSaleVolume.trade_date.desc())
        .all()
    )
