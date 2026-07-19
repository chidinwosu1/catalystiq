"""Bronze -> Silver pipeline for BEA (§9).

    BeaProvider -> BronzeRawDocument -> SilverBeaValue

Idempotent on (provider, dataset, table_name, line_number, time_period,
frequency). Nominal/real/annualized/SA values are distinguished by their
table + unit and never merged.
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
from catalystiq.providers.bea import BeaProvider
from catalystiq.schemas.bea import BeaValue

DOMAIN = "macro"
NORMALIZATION_VERSION = "1.0.0"

_STATUS_BY_CATEGORY = {
    ProviderErrorCategory.RATE_LIMITED: "rate_limited",
    ProviderErrorCategory.TIMEOUT: "unavailable",
    ProviderErrorCategory.NETWORK: "unavailable",
    ProviderErrorCategory.UNAVAILABLE: "unavailable",
}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _doc_id(dataset: str, table_name: str, frequency: str) -> str:
    return f"{dataset}:{table_name}:{frequency}"


def ingest_bea_table(
    provider: BeaProvider, db: Session, dataset: str, table_name: str, frequency: str
) -> models.BronzeIngestionRun:
    identifier = _doc_id(dataset, table_name, frequency)
    run = start_ingestion_run(
        db,
        domain=DOMAIN,
        provider=provider.PROVIDER_NAME,
        adapter_version=provider.ADAPTER_VERSION,
        dataset=f"{dataset}/{table_name}",
        requested_identifier=identifier,
        request_params={"dataset": dataset, "table": table_name, "frequency": frequency},
        license_classification=LicenseClassification.PUBLIC_DOMAIN.value,
        data_classification="revised",
    )
    try:
        values = provider.get_table(dataset, table_name, frequency)
    except ProviderError as exc:
        finish_ingestion_run(
            db, run, status=_STATUS_BY_CATEGORY.get(exc.category, "failed"),
            error_category=exc.category.value, error_detail=str(exc),
        )
        db.commit()
        raise

    store_raw_document(
        db, run, source_identifier=identifier, document_type="bea_table",
        payload={"values": [v.model_dump(mode="json") for v in values]},
    )
    finish_ingestion_run(db, run, status="succeeded", record_count=len(values))
    db.commit()
    db.refresh(run)
    return run


def build_silver_bea(
    db: Session, dataset: str, table_name: str, frequency: str, provider: str = "bea"
) -> int:
    doc = latest_raw_document(
        db, domain=DOMAIN, source_identifier=_doc_id(dataset, table_name, frequency),
        document_type="bea_table",
    )
    if doc is None:
        return 0
    now = _now()
    n = 0
    for raw in doc.payload.get("values", []):
        v = BeaValue(**raw)
        warnings = None if v.value is not None else [{"type": "missing_value", "detail": "no value"}]
        fields = dict(
            stable_identifier=f"{v.dataset}:{v.table_name}:{v.line_number}",
            provider=provider,
            source_record_id=f"{v.table_name}:{v.line_number}:{v.time_period}",
            source_available_at=None,
            effective_at=None,
            retrieved_at=now,
            bronze_ingestion_run_id=doc.ingestion_run_id,
            validation_status="clean_with_warnings" if warnings else "clean",
            data_quality_warnings=warnings,
            normalization_version=NORMALIZATION_VERSION,
            dataset=v.dataset, table_name=v.table_name, line_number=v.line_number,
            line_description=v.line_description, series_code=v.series_code,
            time_period=v.time_period, frequency=v.frequency, value=v.value,
            unit=v.unit, scale=v.scale,
        )
        key = dict(
            provider=provider, dataset=v.dataset, table_name=v.table_name,
            line_number=v.line_number, time_period=v.time_period, frequency=v.frequency,
        )
        existing = db.query(models.SilverBeaValue).filter_by(**key).one_or_none()
        if existing is None:
            db.add(models.SilverBeaValue(created_at=now, **fields))
        else:
            for k, val in fields.items():
                setattr(existing, k, val)
        n += 1
    db.commit()
    return n


def get_silver_bea(db: Session, dataset: str, table_name: str, provider: str = "bea"):
    return (
        db.query(models.SilverBeaValue)
        .filter_by(provider=provider, dataset=dataset, table_name=table_name)
        .order_by(models.SilverBeaValue.time_period, models.SilverBeaValue.line_number)
        .all()
    )
