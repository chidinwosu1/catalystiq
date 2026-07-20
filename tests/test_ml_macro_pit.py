"""Point-in-time BLS/BEA macro: fail-closed + correct vintage read."""
import datetime as dt

from catalystiq.db import models
from catalystiq.ml.features.macro_pit import DEFAULT_CPI_SERIES, pit_macro_features
from catalystiq.ml.features.schema import DataQualityStatus


def _obs(db, *, date, value, realtime_start, series=DEFAULT_CPI_SERIES, provider="bls"):
    db.add(models.SilverMacroObservation(
        stable_identifier=series, provider=provider, series_id=series,
        observation_date=date, value=value, realtime_start=realtime_start, realtime_end=None,
        retrieved_at=dt.datetime(2024, 1, 1), created_at=dt.datetime(2024, 1, 1),
    ))


def _feats(db, ts):
    return {f.feature_name: f for f in pit_macro_features(
        db, "AAPL", ts, as_of=ts.date(), retrieved_at=ts)}


def test_bls_fails_closed_without_vintage(test_db_session):
    # BLS as ingested has realtime_start=None -> no legitimate vintage -> MISSING.
    for m in range(13):
        _obs(test_db_session, date=dt.date(2022, 1, 1) + dt.timedelta(days=30 * m),
             value=100.0 + m, realtime_start=None)
    test_db_session.flush()
    f = _feats(test_db_session, dt.datetime(2023, 6, 1, 20))["macro_cpi_yoy_pit"]
    assert f.data_quality_status is DataQualityStatus.MISSING
    assert f.source_provider == "bls"


def test_bea_gdp_always_fails_closed(test_db_session):
    # No vintage dimension exists for BEA -> always MISSING (never a revised value).
    f = _feats(test_db_session, dt.datetime(2023, 6, 1, 20))["macro_gdp_qoq_pit"]
    assert f.data_quality_status is DataQualityStatus.MISSING
    assert f.source_provider == "bea"


def test_cpi_vintage_read_is_correct_when_vintages_exist(test_db_session):
    # Simulate a vintage-preserving ingestion: each monthly obs has a
    # realtime_start (the release date it became public). 13 months so YoY forms.
    for m in range(13):
        obs_date = dt.date(2022, 1, 1) + dt.timedelta(days=30 * m)
        release = obs_date + dt.timedelta(days=15)  # published ~2 weeks later
        _obs(test_db_session, date=obs_date, value=100.0 + m, realtime_start=release)
    test_db_session.flush()
    # As of well after the 13th release, YoY = (112 - 100)/100 = 0.12.
    f = _feats(test_db_session, dt.datetime(2023, 3, 1, 20))["macro_cpi_yoy_pit"]
    assert f.data_quality_status is DataQualityStatus.OK
    assert abs(f.feature_value - 0.12) < 1e-6
    # available_at is the release (realtime_start), not our ingest time.
    assert f.available_at_timestamp <= dt.datetime(2023, 3, 1, 20)


def test_cpi_vintage_read_excludes_future_release(test_db_session):
    # A revised value released AFTER the prediction date must not be used.
    for m in range(13):
        obs_date = dt.date(2022, 1, 1) + dt.timedelta(days=30 * m)
        _obs(test_db_session, date=obs_date, value=100.0 + m,
             realtime_start=obs_date + dt.timedelta(days=15))
    # A later revision of the latest month, released in the future vs T.
    _obs(test_db_session, date=dt.date(2022, 1, 1) + dt.timedelta(days=30 * 12),
         value=999.0, realtime_start=dt.date(2023, 3, 20))
    test_db_session.flush()
    # T = 2023-03-01 is before the 2023-03-20 revision -> revision ignored.
    f = _feats(test_db_session, dt.datetime(2023, 3, 1, 20))["macro_cpi_yoy_pit"]
    assert abs(f.feature_value - 0.12) < 1e-6  # not the 999 revision
