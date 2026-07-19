"""Project existing stored records into the shared PointInTimeProvenance
contract, reconciling the legacy column names rather than duplicating fields.

- SilverRecordMixin rows: provider -> source_provider (canonicalized),
  effective_at -> source_event_timestamp, source_available_at ->
  available_at_timestamp (falls back to retrieved_at, since we could not have
  known a value before we retrieved it), retrieved_at -> retrieved_at_timestamp,
  validation_status -> data_quality_status.
- SilverPriceBar has no provider columns of its own; project it via its Bronze
  ingestion run (provider, timestamps) plus the bar's own event date/quality.
"""
from __future__ import annotations

import datetime as dt

from catalystiq.provenance.contract import (
    DataQualityStatus,
    PointInTimeProvenance,
    canonical_provider,
    data_quality_status_from_validation,
)


def provenance_from_silver(
    record,
    *,
    source_event_timestamp: dt.datetime | None = None,
    frequency: str | None = None,
    source_dataset: str | None = None,
    source_series_id: str | None = None,
    source_url: str | None = None,
    license_policy_id: str | None = None,
) -> PointInTimeProvenance:
    """Build the shared contract from a SilverRecordMixin row. Pass the domain's
    real event time (observation_date, session_date, trade_date, filing_date,
    ...) as `source_event_timestamp`; it falls back to the row's effective_at."""
    retrieved = getattr(record, "retrieved_at", None)
    available = getattr(record, "source_available_at", None) or retrieved
    event = source_event_timestamp
    if event is None:
        event = getattr(record, "effective_at", None)
    return PointInTimeProvenance(
        source_provider=canonical_provider(getattr(record, "provider", None)),
        source_event_timestamp=event,
        available_at_timestamp=available,
        retrieved_at_timestamp=retrieved,
        data_quality_status=data_quality_status_from_validation(
            getattr(record, "validation_status", None)
        ),
        source_record_id=getattr(record, "source_record_id", None),
        source_dataset=source_dataset,
        source_series_id=source_series_id,
        source_url=source_url or getattr(record, "source_url", None),
        license_policy_id=license_policy_id,
        frequency=frequency,
    )


def provenance_from_bronze_run(
    run,
    *,
    source_event_timestamp: dt.datetime | None,
    data_quality_status: DataQualityStatus | str = DataQualityStatus.VALID,
    source_record_id: str | None = None,
    source_dataset: str | None = None,
    source_url: str | None = None,
    license_policy_id: str | None = None,
    frequency: str | None = "1d",
) -> PointInTimeProvenance:
    """Build the shared contract for a record whose provenance lives on its
    Bronze ingestion run (e.g. SilverPriceBar). Uses the run's provider and
    timestamps; the caller supplies the record's own event time and quality."""
    retrieved = getattr(run, "completed_at", None) or getattr(run, "requested_at", None)
    available = getattr(run, "release_timestamp", None) or retrieved
    quality = (
        data_quality_status
        if isinstance(data_quality_status, DataQualityStatus)
        else data_quality_status_from_validation(data_quality_status)
    )
    return PointInTimeProvenance(
        source_provider=canonical_provider(getattr(run, "provider", None)),
        source_event_timestamp=source_event_timestamp,
        available_at_timestamp=available,
        retrieved_at_timestamp=retrieved,
        data_quality_status=quality,
        source_record_id=source_record_id,
        source_dataset=source_dataset or getattr(run, "dataset", None),
        source_url=source_url or getattr(run, "endpoint", None),
        license_policy_id=license_policy_id or getattr(run, "license_classification", None),
        frequency=frequency,
    )
