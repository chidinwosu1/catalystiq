"""Point-in-time provenance contract: canonicalization, quality mapping,
dynamic freshness, temporal-ordering validation, the ML lookahead guard, and
projection from existing records."""
from __future__ import annotations

import ast
import datetime as dt
import pathlib

import pytest

from catalystiq.pipelines.freshness import FreshnessPolicy
from catalystiq.provenance import (
    DataQualityStatus,
    Freshness,
    LookaheadViolation,
    PointInTimeProvenance,
    assert_point_in_time_safe,
    canonical_provider,
    compute_freshness,
    data_quality_status_from_validation,
    is_point_in_time_safe,
    provenance_from_bronze_run,
    provenance_from_silver,
    validate_temporal_ordering,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_UTC = dt.timezone.utc


def _d(y, m, day, h=12) -> dt.datetime:
    return dt.datetime(y, m, day, h, tzinfo=_UTC)


# --- provider canonicalization -----------------------------------------


def test_canonical_provider():
    assert canonical_provider("YahooFinanceProvider") == "yahoo"
    assert canonical_provider("fred") == "fred_restricted"
    assert canonical_provider("WebullBroker") == "webull"
    assert canonical_provider("sec_edgar") == "sec_edgar"
    assert canonical_provider("BLS") == "bls"
    assert canonical_provider(None) is None


# --- data_quality_status reconciliation --------------------------------


def test_quality_status_mapping():
    assert data_quality_status_from_validation("clean") is DataQualityStatus.OK
    # clean_with_warnings -> OK (usable); the warning reason stays in the row's
    # data_quality_warnings JSON, so nothing is discarded.
    assert data_quality_status_from_validation("clean_with_warnings") is DataQualityStatus.OK
    assert data_quality_status_from_validation("quarantined") is DataQualityStatus.INVALID
    assert data_quality_status_from_validation("insufficient_data") is DataQualityStatus.MISSING
    # Unknown / empty -> FAIL CLOSED to INVALID, never silently OK.
    assert data_quality_status_from_validation("wat") is DataQualityStatus.INVALID
    assert data_quality_status_from_validation(None) is DataQualityStatus.INVALID


def test_quality_enum_matches_ml_feature_contract():
    # The provenance enum must stay value-identical to the ML feature contract's
    # DataQualityStatus so a provenance record and an ML feature share literals.
    from catalystiq.ml.features.schema import DataQualityStatus as MlDataQualityStatus

    assert {s.value for s in DataQualityStatus} == {s.value for s in MlDataQualityStatus}


# --- dynamic freshness -------------------------------------------------


def test_freshness_daily_via_policy():
    policy = FreshnessPolicy()
    now = _d(2026, 7, 20)  # Monday mid-session (last closed session = Fri 07-17)
    assert compute_freshness(now=now, source_event_timestamp=_d(2026, 7, 17), policy=policy) is Freshness.CURRENT
    assert compute_freshness(now=now, source_event_timestamp=_d(2026, 7, 10), policy=policy) is Freshness.STALE


def test_freshness_future_dated_and_unknown():
    now = _d(2026, 7, 20)
    assert compute_freshness(now=now, source_event_timestamp=_d(2026, 7, 25)) is Freshness.FUTURE_DATED
    assert (
        compute_freshness(
            now=now, source_event_timestamp=None, available_at_timestamp=_d(2026, 8, 1)
        )
        is Freshness.FUTURE_DATED
    )
    assert compute_freshness(now=now, source_event_timestamp=None) is Freshness.UNKNOWN


def test_freshness_monthly_age_based():
    now = _d(2026, 7, 20)
    assert (
        compute_freshness(now=now, source_event_timestamp=_d(2026, 7, 1), frequency="monthly")
        is Freshness.CURRENT
    )
    assert (
        compute_freshness(now=now, source_event_timestamp=_d(2026, 4, 1), frequency="monthly")
        is Freshness.STALE
    )


def test_freshness_is_recomputed_not_persisted():
    # The same record is current soon after, stale much later - proving freshness
    # must be computed at evaluation time, never frozen.
    prov = PointInTimeProvenance(
        source_provider="bls", source_event_timestamp=_d(2026, 7, 1),
        available_at_timestamp=_d(2026, 7, 1), retrieved_at_timestamp=_d(2026, 7, 1),
        data_quality_status=DataQualityStatus.OK, frequency="monthly",
    )
    assert prov.freshness(now=_d(2026, 7, 20)) is Freshness.CURRENT
    assert prov.freshness(now=_d(2026, 12, 1)) is Freshness.STALE


# --- temporal ordering -------------------------------------------------


def test_temporal_ordering_invariant():
    ok = validate_temporal_ordering(_d(2026, 7, 1), _d(2026, 7, 2), _d(2026, 7, 3))
    assert ok == []
    # available after retrieved -> violation.
    assert validate_temporal_ordering(_d(2026, 7, 1), _d(2026, 7, 5), _d(2026, 7, 3))
    # event after available -> violation, UNLESS it's a documented correction.
    assert validate_temporal_ordering(_d(2026, 7, 5), _d(2026, 7, 2), _d(2026, 7, 6))
    assert validate_temporal_ordering(
        _d(2026, 7, 5), _d(2026, 7, 2), _d(2026, 7, 6), is_correction=True
    ) == []


# --- ML lookahead guard ------------------------------------------------


def test_lookahead_guard():
    pred = _d(2026, 7, 20)
    assert is_point_in_time_safe(_d(2026, 7, 19), pred) is True
    assert is_point_in_time_safe(_d(2026, 7, 21), pred) is False
    assert is_point_in_time_safe(None, pred) is False  # unknown availability is unsafe

    assert_point_in_time_safe(_d(2026, 7, 19), pred)  # ok, no raise
    with pytest.raises(LookaheadViolation):
        assert_point_in_time_safe(_d(2026, 7, 21), pred)  # future leak
    with pytest.raises(LookaheadViolation):
        assert_point_in_time_safe(None, pred)  # unknown -> unsafe


# --- projection from existing records ----------------------------------


class _FakeSilver:
    provider = "YahooFinanceProvider"
    effective_at = None
    source_available_at = None
    retrieved_at = _d(2026, 7, 18)
    validation_status = "clean_with_warnings"
    source_record_id = "rec-1"
    source_url = None


def test_projection_from_silver_reconciles_columns():
    prov = provenance_from_silver(
        _FakeSilver(), source_event_timestamp=_d(2026, 7, 17), frequency="1d"
    )
    assert prov.source_provider == "yahoo"  # canonicalized from class name
    assert prov.source_event_timestamp == _d(2026, 7, 17)
    # source_available_at is null -> falls back to retrieved (we couldn't have
    # known it earlier), preserving available_at <= retrieved_at.
    assert prov.available_at_timestamp == prov.retrieved_at_timestamp == _d(2026, 7, 18)
    assert prov.data_quality_status is DataQualityStatus.OK  # clean_with_warnings -> OK
    assert prov.source_record_id == "rec-1"
    assert prov.temporal_violations() == []


class _FakeRun:
    provider = "YahooFinanceProvider"
    completed_at = _d(2026, 7, 18)
    requested_at = _d(2026, 7, 18)
    release_timestamp = None
    dataset = "timeseries"
    endpoint = "https://api.example.com/timeseries"
    license_classification = "public_domain"


def test_projection_from_bronze_run_for_price_bar():
    prov = provenance_from_bronze_run(
        _FakeRun(), source_event_timestamp=_d(2026, 7, 17), data_quality_status="clean"
    )
    assert prov.source_provider == "yahoo"
    assert prov.available_at_timestamp == prov.retrieved_at_timestamp == _d(2026, 7, 18)
    assert prov.data_quality_status is DataQualityStatus.OK
    assert prov.source_dataset == "timeseries"
    assert prov.license_policy_id == "public_domain"
    assert prov.temporal_violations() == []


# --- isolation: contract is pure, FRED stays ephemeral -----------------


def test_provenance_package_is_pure():
    # The contract must not import db/pipelines(except freshness)/fred, so it
    # stays a reusable pure layer and FRED never enters it.
    pkg = _REPO_ROOT / "catalystiq" / "provenance"
    forbidden = ("catalystiq.db", "catalystiq.fred")
    for path in pkg.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                assert not any(m.startswith(f) for f in forbidden), f"{path.name} imports {m}"
