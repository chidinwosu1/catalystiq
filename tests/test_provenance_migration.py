"""Provenance schema migration + point-in-time retrieval.

Two concerns, both required by the ML feature manifest:

1. The Alembic migration `a1b2c3d4e5f6` is exercised end-to-end against a real
   (temp) database seeded with *legacy* rows: it must add the shared point-in-time
   columns, backfill `data_quality_status` into the canonical ML vocabulary
   (failing closed to `invalid` for unknown legacy statuses), populate
   `source_available_at`, and canonicalize the market-price provider - all
   without touching the retained legacy `validation_status`.

2. The resulting Silver rows support point-in-time feature retrieval with no
   look-ahead leakage: a daily bar for date D is available only after D's close,
   and a query "as of" a prediction timestamp returns only rows that were
   knowable then.
"""
from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from catalystiq.config import get_settings
from catalystiq.db import models
from catalystiq.provenance import (
    DataQualityStatus,
    canonical_provider,
    is_point_in_time_safe,
    provenance_from_bronze_run,
)

_UTC = dt.timezone.utc
_BEFORE = "f9a2c1d4e8b7"  # revision just before the provenance migration
_TARGET = "a1b2c3d4e5f6"  # the provenance migration under test


# --- helpers -----------------------------------------------------------------


def _alembic_config(db_path) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _minimal_row(conn, table: str, overrides: dict) -> dict:
    """Build a row supplying a type-appropriate dummy for every NOT NULL column
    that lacks a default and isn't overridden, so we can INSERT into a wide
    Silver table without hand-listing three dozen columns."""
    info = conn.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    row = dict(overrides)
    for _cid, name, coltype, notnull, default, pk in info:
        if name in row or default is not None or pk:
            continue
        if not notnull:
            continue
        t = (coltype or "").upper()
        if "INT" in t:
            row[name] = 0
        elif any(k in t for k in ("REAL", "FLOA", "DOUB", "NUM")):
            row[name] = 0.0
        elif "DATE" in t or "TIME" in t:
            row[name] = "2026-01-05 00:00:00"
        elif "BOOL" in t:
            row[name] = 0
        else:
            row[name] = "x"
    return row


def _insert(conn, table: str, row: dict) -> None:
    cols = ", ".join(row)
    binds = ", ".join(f":{c}" for c in row)
    conn.execute(sa.text(f"INSERT INTO {table} ({cols}) VALUES ({binds})"), row)


@pytest.fixture
def migrated_legacy_db(tmp_path, monkeypatch):
    """Migrate a temp DB to the revision *before* the provenance migration, seed
    legacy rows, then run the migration and hand back a connection to inspect."""
    db_path = tmp_path / "legacy.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    cfg = _alembic_config(db_path)

    command.upgrade(cfg, _BEFORE)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        # A mixin Silver table (market session) with a spread of legacy statuses,
        # all with source_available_at absent (the column doesn't exist yet).
        legacy_quality = {
            "clean": "ok",
            "clean_with_warnings": "ok",
            "quarantined": "invalid",
            "insufficient_data": "missing",
            "totally-unknown-status": "invalid",  # FAIL CLOSED
        }
        for i, status in enumerate(legacy_quality):
            _insert(
                conn,
                "silver_market_session",
                _minimal_row(
                    conn,
                    "silver_market_session",
                    {
                        "exchange": "NYSE",
                        "session_date": f"2026-01-0{i + 1}",
                        "stable_identifier": f"NYSE:2026-01-0{i + 1}",
                        "provider": "nyse",
                        "validation_status": status,
                        "retrieved_at": "2026-01-10 12:00:00",
                        "created_at": "2026-01-10 12:00:00",
                    },
                ),
            )

        # A Yahoo bronze run (legacy class-name provider) + a legacy price bar
        # with source_available_at absent and legacy data_quality_status "clean".
        _insert(
            conn,
            "bronze_ingestion_run",
            _minimal_row(
                conn,
                "bronze_ingestion_run",
                {
                    "id": 1,
                    "domain": "market_price",
                    "provider": "YahooFinanceProvider",
                    "status": "succeeded",
                    "requested_at": "2026-01-10 12:00:00",
                },
            ),
        )
        _insert(
            conn,
            "silver_price_bar",
            _minimal_row(
                conn,
                "silver_price_bar",
                {
                    "date": "2026-01-06",
                    "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 1000,
                    "data_quality_status": "clean",
                    "created_at": "2026-01-10 12:00:00",
                    "updated_at": "2026-01-10 12:00:00",
                },
            ),
        )

    command.upgrade(cfg, _TARGET)
    engine.dispose()

    engine = sa.create_engine(f"sqlite:///{db_path}")
    conn = engine.connect()
    try:
        yield conn, legacy_quality
    finally:
        conn.close()
        engine.dispose()
        get_settings.cache_clear()


# --- migration backfill ------------------------------------------------------


def test_migration_adds_provenance_columns(migrated_legacy_db):
    conn, _ = migrated_legacy_db
    cols = {r[1] for r in conn.execute(sa.text("PRAGMA table_info(silver_market_session)"))}
    for expected in (
        "data_quality_status", "source_available_at", "source_dataset",
        "source_series_id", "source_url", "license_policy_id",
    ):
        assert expected in cols, f"{expected} missing after migration"


def test_migration_backfills_quality_failing_closed(migrated_legacy_db):
    conn, legacy_quality = migrated_legacy_db
    rows = conn.execute(
        sa.text(
            "SELECT validation_status, data_quality_status FROM silver_market_session"
        )
    ).fetchall()
    assert rows, "expected seeded rows to survive the migration"
    for validation_status, quality in rows:
        # The legacy column is RETAINED (auditability) ...
        assert validation_status in legacy_quality
        # ... and the canonical ML status is backfilled, unknown -> invalid.
        assert quality == legacy_quality[validation_status]
        assert quality in {s.value for s in DataQualityStatus}


def test_migration_populates_source_available_at_on_mixin(migrated_legacy_db):
    conn, _ = migrated_legacy_db
    rows = conn.execute(
        sa.text("SELECT source_available_at, retrieved_at FROM silver_market_session")
    ).fetchall()
    assert rows, "expected seeded rows to survive the migration"
    for available, retrieved in rows:
        assert available is not None
        # available_at is a safe floor: it never precedes retrieval and never
        # claims a value was knowable before we retrieved it.
        assert available == retrieved


def test_migration_price_bar_available_at_is_end_of_day(migrated_legacy_db):
    conn, _ = migrated_legacy_db
    date_str, available_str = conn.execute(
        sa.text("SELECT date, source_available_at FROM silver_price_bar")
    ).one()
    bar_date = dt.date.fromisoformat(str(date_str)[:10])
    available = dt.datetime.fromisoformat(str(available_str))
    # A daily bar is knowable only after its own session closes: the floor is
    # end-of-day of the bar date, never earlier (no look-ahead).
    assert available.date() == bar_date
    assert available.hour == 23
    assert available >= dt.datetime.combine(bar_date, dt.time(0, 0))


def test_migration_price_bar_quality_revocabbed(migrated_legacy_db):
    conn, _ = migrated_legacy_db
    quality = conn.execute(
        sa.text("SELECT data_quality_status FROM silver_price_bar")
    ).scalar_one()
    assert quality == "ok"  # legacy "clean" -> canonical ML "ok"


def test_migration_canonicalizes_market_price_provider(migrated_legacy_db):
    conn, _ = migrated_legacy_db
    provider = conn.execute(
        sa.text("SELECT provider FROM bronze_ingestion_run WHERE id = 1")
    ).scalar_one()
    assert provider == "yahoo"  # YahooFinanceProvider -> yahoo
    assert provider == canonical_provider("YahooFinanceProvider")


def test_migration_downgrade_drops_added_columns(tmp_path, monkeypatch):
    db_path = tmp_path / "rollback.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, _TARGET)
    command.downgrade(cfg, _BEFORE)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            cols = {r[1] for r in conn.execute(sa.text("PRAGMA table_info(silver_market_session)"))}
        assert "source_dataset" not in cols
        assert "license_policy_id" not in cols
    finally:
        engine.dispose()
        get_settings.cache_clear()


# --- point-in-time retrieval / no leakage (ORM level) ------------------------


def _make_price_bars(session, days: list[dt.date]) -> models.BronzeIngestionRun:
    # The batch is fetched the morning after the latest bar closed, so the run's
    # retrieval time never precedes the bars it delivers.
    fetched_at = dt.datetime.combine(max(days) + dt.timedelta(days=1), dt.time(9, 0))
    run = models.BronzeIngestionRun(
        domain="market_price",
        requested_symbol="AAPL",
        provider=canonical_provider("YahooFinanceProvider"),
        status="succeeded",
        requested_at=fetched_at,
        completed_at=fetched_at,
    )
    session.add(run)
    session.flush()
    ticker = models.Ticker(symbol="AAPL")
    session.add(ticker)
    session.flush()
    for d in days:
        session.add(
            models.SilverPriceBar(
                ticker_id=ticker.id, date=d,
                open=10.0, high=11.0, low=9.0, close=10.5, volume=1000,
                source_bronze_ingestion_run_id=run.id,
                # The pipeline's point-in-time floor: end-of-day of the bar date.
                source_available_at=dt.datetime.combine(d, dt.time(23, 59, 59)),
                data_quality_status="ok",
                created_at=dt.datetime(2026, 2, 1, 12),
                updated_at=dt.datetime(2026, 2, 1, 12),
            )
        )
    session.commit()
    return run


def test_price_bar_available_at_prevents_lookahead(test_db_session):
    bar_date = dt.date(2026, 3, 10)
    _make_price_bars(test_db_session, [bar_date])
    bar = test_db_session.query(models.SilverPriceBar).one()
    available = bar.source_available_at.replace(tzinfo=_UTC)

    # A prediction made intraday on the bar date must NOT see that day's bar.
    intraday = dt.datetime(2026, 3, 10, 15, tzinfo=_UTC)
    assert is_point_in_time_safe(available, intraday) is False
    # The next morning it is knowable.
    next_open = dt.datetime(2026, 3, 11, 9, 30, tzinfo=_UTC)
    assert is_point_in_time_safe(available, next_open) is True


def test_point_in_time_query_returns_only_knowable_bars(test_db_session):
    days = [dt.date(2026, 3, d) for d in (9, 10, 11, 12)]
    _make_price_bars(test_db_session, days)

    # "As of" the morning of the 11th, only the 9th and 10th had closed.
    as_of = dt.datetime(2026, 3, 11, 9, 30)
    visible = (
        test_db_session.query(models.SilverPriceBar)
        .filter(models.SilverPriceBar.source_available_at <= as_of)
        .order_by(models.SilverPriceBar.date)
        .all()
    )
    assert [b.date for b in visible] == [dt.date(2026, 3, 9), dt.date(2026, 3, 10)]


def test_projection_of_price_bar_is_temporally_consistent(test_db_session):
    bar_date = dt.date(2026, 3, 10)
    run = _make_price_bars(test_db_session, [bar_date])
    bar = test_db_session.query(models.SilverPriceBar).one()
    # Project via the bar's own point-in-time floor (its source_available_at),
    # which is more precise than the batch fetch time.
    prov = provenance_from_bronze_run(
        run,
        source_event_timestamp=dt.datetime.combine(bar_date, dt.time(23, 59, 59)),
        available_at_timestamp=bar.source_available_at,
        data_quality_status=bar.data_quality_status,
    )
    assert prov.source_provider == "yahoo"
    assert prov.data_quality_status is DataQualityStatus.OK
    assert prov.temporal_violations() == []
    # The guard raises for an intraday prediction, passes the next morning.
    from catalystiq.provenance import LookaheadViolation

    with pytest.raises(LookaheadViolation):
        prov.assert_usable_for_prediction(dt.datetime(2026, 3, 10, 15, tzinfo=_UTC))
    prov.assert_usable_for_prediction(dt.datetime(2026, 3, 11, 9, 30, tzinfo=_UTC))
