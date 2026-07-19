"""Shared point-in-time provenance contract.

ONE reusable definition of the point-in-time facts every persisted record
carries, so providers don't each redefine them. Five facts are persisted
(source_provider, source_event_timestamp, available_at_timestamp,
retrieved_at_timestamp, data_quality_status) plus optional source identity;
`freshness` is the sixth field but is ALWAYS computed dynamically, never
persisted (a record marked "current" today would be wrong tomorrow).

This module reconciles with the existing SilverRecordMixin columns rather than
duplicating them (provider -> source_provider, effective_at ->
source_event_timestamp, source_available_at -> available_at_timestamp,
retrieved_at -> retrieved_at_timestamp, validation_status ->
data_quality_status). The restricted FRED integration stays ephemeral and is
excluded from all persisted provenance / ML feature pipelines.

When the canonical ML feature manifest is merged, map these records to it and
REPORT any incompatible field definitions — do not silently change this
contract.
"""
from catalystiq.provenance.contract import (
    DataQualityStatus,
    Freshness,
    LookaheadViolation,
    PointInTimeProvenance,
    assert_point_in_time_safe,
    canonical_provider,
    compute_freshness,
    data_quality_status_from_validation,
    is_point_in_time_safe,
    validate_temporal_ordering,
)
from catalystiq.provenance.projection import (
    provenance_from_bronze_run,
    provenance_from_silver,
)

__all__ = [
    "DataQualityStatus",
    "Freshness",
    "LookaheadViolation",
    "PointInTimeProvenance",
    "assert_point_in_time_safe",
    "canonical_provider",
    "compute_freshness",
    "data_quality_status_from_validation",
    "is_point_in_time_safe",
    "validate_temporal_ordering",
    "provenance_from_silver",
    "provenance_from_bronze_run",
]
