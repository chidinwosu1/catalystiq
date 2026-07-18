"""Shared Bronze ingestion helpers for the network/document data domains.

The price-bar pipeline (market_price_pipeline.py) predates this and keeps its
own bespoke Bronze tables/flow. Everything added from Phase 2 on - NYSE
calendar, FRED/ALFRED, SEC EDGAR - records ingestion through these helpers so
every run populates the generalized BronzeIngestionRun fields (§3) uniformly
and stores its raw payloads in the generic BronzeRawDocument table, with
secrets stripped from the persisted request params and a checksum on every
payload.

These functions never call a provider or normalize anything - they only
persist ingestion bookkeeping. The adapter fetches; the caller passes the
result here; the Silver builder reads BronzeRawDocument later.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json

from sqlalchemy.orm import Session

from catalystiq.db import models
from catalystiq.providers.transport import redact


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def payload_checksum(payload) -> str:
    """Stable SHA-256 of a JSON-serializable payload (sorted keys, so key
    order never changes the digest). `default=str` tolerates dates/datetimes."""
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def start_ingestion_run(
    db: Session,
    *,
    domain: str,
    provider: str,
    adapter_version: str | None = None,
    dataset: str | None = None,
    endpoint: str | None = None,
    requested_identifier: str | None = None,
    requested_symbol: str | None = None,
    request_params: dict | None = None,
    license_classification: str | None = None,
    data_classification: str | None = None,
) -> models.BronzeIngestionRun:
    """Open a BronzeIngestionRun in `running` state. `request_params` is
    stored with secrets redacted (never the raw api_key/token/etc.)."""
    now = _now()
    # requested_symbol is a legacy NOT NULL String(15); fall back to the
    # generalized identifier (truncated) so non-market domains satisfy it.
    symbol = (requested_symbol or requested_identifier or "")[:15]
    run = models.BronzeIngestionRun(
        domain=domain,
        requested_symbol=symbol,
        requested_identifier=requested_identifier,
        dataset=dataset,
        endpoint=endpoint,
        request_params=redact(request_params) if request_params else None,
        provider=provider,
        provider_adapter_version=adapter_version,
        license_classification=license_classification,
        data_classification=data_classification,
        requested_at=now,
        started_at=now,
        status="running",
    )
    db.add(run)
    db.flush()
    return run


def finish_ingestion_run(
    db: Session,
    run: models.BronzeIngestionRun,
    *,
    status: str,
    record_count: int | None = None,
    http_status: int | None = None,
    response_timestamp: dt.datetime | None = None,
    release_timestamp: dt.datetime | None = None,
    rate_limit_info: dict | None = None,
    retry_count: int = 0,
    error_category: str | None = None,
    error_detail: str | None = None,
) -> models.BronzeIngestionRun:
    """Close out a run with its terminal status and metadata. `error_detail`
    must already be sanitized by the caller (adapters raise ProviderError
    with secret-free messages); it's truncated defensively here."""
    run.status = status
    run.completed_at = _now()
    run.record_count = record_count
    run.http_status = http_status
    run.response_timestamp = response_timestamp
    run.release_timestamp = release_timestamp
    run.rate_limit_info = rate_limit_info
    run.retry_count = retry_count or 0
    run.error_category = error_category
    if error_detail:
        run.error_detail = error_detail[:1000]
    db.flush()
    return run


def store_raw_document(
    db: Session,
    run: models.BronzeIngestionRun,
    *,
    source_identifier: str,
    document_type: str,
    payload: dict,
    source_url: str | None = None,
    source_timestamp: dt.datetime | None = None,
) -> models.BronzeRawDocument:
    """Persist one raw provider payload under `run`, with a checksum."""
    doc = models.BronzeRawDocument(
        ingestion_run_id=run.id,
        domain=run.domain,
        source_identifier=source_identifier,
        document_type=document_type,
        payload=payload,
        payload_checksum=payload_checksum(payload),
        source_url=source_url,
        source_timestamp=source_timestamp,
        fetched_at=_now(),
    )
    db.add(doc)
    db.flush()
    return doc


def latest_raw_document(
    db: Session,
    *,
    domain: str,
    source_identifier: str,
    document_type: str,
) -> models.BronzeRawDocument | None:
    """Most recent raw document of a type for an identifier - the current
    best-known raw payload a Silver build should normalize from."""
    return (
        db.query(models.BronzeRawDocument)
        .filter_by(
            domain=domain,
            source_identifier=source_identifier,
            document_type=document_type,
        )
        .order_by(models.BronzeRawDocument.id.desc())
        .first()
    )
