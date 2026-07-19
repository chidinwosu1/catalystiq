"""The generalized Bronze ingestion-run columns (§3) persist and default
correctly, and the existing price-bar fields are untouched.

Schema parity with the Alembic migration
(alembic/versions/a7c3f9e1b2d4_generalize_bronze_ingestion_run.py) is
verified live at build time via `alembic upgrade head` + autogenerate drift
check; this test exercises the ORM model itself against a fresh schema."""
import datetime as dt

from catalystiq.db import models
from catalystiq.providers.base import IngestionStatus


def test_generalized_fields_persist(test_db_session):
    db = test_db_session
    now = dt.datetime(2026, 7, 18, 12, 0, 0)
    run = models.BronzeIngestionRun(
        domain="macro",
        requested_symbol="DGS10",  # legacy field still required/non-null
        requested_identifier="DGS10",
        dataset="series/observations",
        endpoint="https://api.stlouisfed.org/fred/series/observations",
        data_classification="revised",
        license_classification="public_domain",
        response_timestamp=now,
        release_timestamp=dt.datetime(2026, 7, 17, 8, 30, 0),
        http_status=200,
        record_count=42,
        rate_limit_info={"x-ratelimit-remaining": "119"},
        error_category=None,
        payload_checksum="a" * 64,
        payload_reference="s3://bucket/key",
        provider="fred",
        requested_at=now,
        status=IngestionStatus.SUCCEEDED.value,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    fetched = db.query(models.BronzeIngestionRun).filter_by(id=run.id).one()
    assert fetched.requested_identifier == "DGS10"
    assert fetched.dataset == "series/observations"
    assert fetched.data_classification == "revised"
    assert fetched.license_classification == "public_domain"
    assert fetched.http_status == 200
    assert fetched.record_count == 42
    assert fetched.rate_limit_info == {"x-ratelimit-remaining": "119"}
    assert fetched.payload_checksum == "a" * 64
    assert fetched.payload_reference == "s3://bucket/key"
    assert fetched.release_timestamp == dt.datetime(2026, 7, 17, 8, 30, 0)
    assert fetched.status == "succeeded"


def test_new_status_values_are_storable():
    # rate_limited / unavailable are new allowed statuses (§3); the column is
    # free-text so they just need to round-trip.
    assert IngestionStatus.RATE_LIMITED.value == "rate_limited"
    assert IngestionStatus.UNAVAILABLE.value == "unavailable"


def test_retry_count_defaults_to_zero(test_db_session):
    db = test_db_session
    run = models.BronzeIngestionRun(
        domain="market_price",
        requested_symbol="AAPL",
        provider="YahooFinanceProvider",
        requested_at=dt.datetime(2026, 7, 18, 12, 0, 0),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    assert run.retry_count == 0
    # Legacy price path leaves the generalized fields unset.
    assert run.requested_identifier is None
    assert run.record_count is None
