"""SilverPitFeatureProvider: point-in-time correctness, no look-ahead leakage,
honest missingness, and end-to-end drive of the training-example builder.

All data here is synthetic Silver written directly into the test DB; no external
provider is called. The point of the suite is the *temporal* contract, not the
numeric values of the indicators (those are covered by the analysis-engine
tests).
"""
from __future__ import annotations

import datetime as dt

import pytest

from catalystiq.db import models
from catalystiq.features import SilverPitFeatureProvider
from catalystiq.ml.dataset.builder import ExampleRequest, TrainingExampleBuilder
from catalystiq.ml.features.schema import (
    DataQualityStatus,
    build_feature_vector,
    validate_feature,
)

_UTC = dt.timezone.utc


def _eod(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(23, 59, 59))


def _seed_bars(session, symbol: str, start: dt.date, n: int, *, base: float = 100.0):
    """Write `n` consecutive daily Silver bars with a gently rising close and a
    point-in-time floor of end-of-day of each bar date."""
    ticker = models.Ticker(symbol=symbol)
    session.add(ticker)
    session.flush()
    day = start
    added = 0
    while added < n:
        if day.weekday() < 5:  # weekdays only, loosely mimicking sessions
            price = base + added * 0.5
            session.add(
                models.SilverPriceBar(
                    ticker_id=ticker.id,
                    date=day,
                    open=price - 0.2,
                    high=price + 1.0,
                    low=price - 1.0,
                    close=price,
                    volume=1_000_000 + added * 1000,
                    source_bronze_ingestion_run_id=None,
                    source_available_at=_eod(day),
                    data_quality_status="ok",
                    created_at=dt.datetime(2020, 1, 1),
                    updated_at=_eod(day),
                )
            )
            added += 1
        day += dt.timedelta(days=1)
    session.commit()
    return ticker


def _bar_dates(session, symbol: str) -> list[dt.date]:
    ticker = session.query(models.Ticker).filter_by(symbol=symbol).one()
    return [
        r.date
        for r in session.query(models.SilverPriceBar)
        .filter_by(ticker_id=ticker.id)
        .order_by(models.SilverPriceBar.date)
        .all()
    ]


# --- point-in-time visibility / no leakage -----------------------------------


def test_features_only_see_bars_available_at_prediction(test_db_session):
    _seed_bars(test_db_session, "AAPL", dt.date(2025, 1, 1), 60)
    dates = _bar_dates(test_db_session, "AAPL")
    # Predict right after the 30th bar closes: only 30 bars are knowable.
    cutoff = dates[29]
    pred = dt.datetime.combine(cutoff, dt.time(23, 59, 59), tzinfo=_UTC)

    provider = SilverPitFeatureProvider(test_db_session)
    feats = provider.get_features("AAPL", pred)

    # Every feature's availability must be at or before the prediction instant.
    for f in feats:
        assert f.available_at_timestamp <= pred
        assert f.source_event_timestamp <= f.available_at_timestamp
    # adj_close reflects the 30th bar, never a later one.
    adj_close = next(f for f in feats if f.feature_name == "adj_close")
    assert adj_close.feature_value == pytest.approx(100.0 + 29 * 0.5)


def test_future_bar_does_not_leak_into_features(test_db_session):
    _seed_bars(test_db_session, "AAPL", dt.date(2025, 1, 1), 60)
    dates = _bar_dates(test_db_session, "AAPL")
    cutoff = dates[29]
    # A prediction one microsecond before the 31st bar becomes available.
    pred = dt.datetime.combine(dates[30], dt.time(23, 59, 59), tzinfo=_UTC) - dt.timedelta(microseconds=1)

    provider = SilverPitFeatureProvider(test_db_session)
    feats = provider.get_features("AAPL", pred)
    adj_close = next(f for f in feats if f.feature_name == "adj_close")
    # The 31st bar (index 30) must NOT influence the as-of close.
    assert adj_close.feature_value == pytest.approx(100.0 + 29 * 0.5)
    assert adj_close.source_event_timestamp.date() == cutoff


def test_bars_with_unknown_availability_are_excluded(test_db_session):
    ticker = _seed_bars(test_db_session, "AAPL", dt.date(2025, 1, 1), 40)
    # Null out availability on the newest 5 bars: they can't be proven knowable.
    newest = (
        test_db_session.query(models.SilverPriceBar)
        .filter_by(ticker_id=ticker.id)
        .order_by(models.SilverPriceBar.date.desc())
        .limit(5)
        .all()
    )
    for r in newest:
        r.source_available_at = None
    test_db_session.commit()

    dates = _bar_dates(test_db_session, "AAPL")
    pred = dt.datetime.combine(dates[-1], dt.time(23, 59, 59), tzinfo=_UTC)
    provider = SilverPitFeatureProvider(test_db_session)
    feats = provider.get_features("AAPL", pred)
    adj_close = next(f for f in feats if f.feature_name == "adj_close")
    # The newest bar with known availability is index 34 (35th bar).
    assert adj_close.feature_value == pytest.approx(100.0 + 34 * 0.5)


# --- schema admissibility / honest missingness -------------------------------


def test_all_features_pass_the_ml_schema_gate(test_db_session):
    _seed_bars(test_db_session, "AAPL", dt.date(2024, 1, 1), 260)
    dates = _bar_dates(test_db_session, "AAPL")
    pred = dt.datetime.combine(dates[-1], dt.time(23, 59, 59), tzinfo=_UTC)
    provider = SilverPitFeatureProvider(test_db_session)
    feats = provider.get_features("AAPL", pred)

    # No feature is rejected for leakage / licensing / provenance.
    for f in feats:
        rej = validate_feature(f, for_training=True)
        assert rej is None, f"{f.feature_name} rejected: {rej}"

    # Strict assembly (used for real training) must not raise.
    vector, rejections = build_feature_vector(feats, for_training=True, strict=True)
    assert rejections == []
    # A rich window computes the core price/technical features.
    for name in ("adj_close", "rsi_14", "sma_50", "macd", "atr_14", "log_return_5d"):
        assert vector.get(name) is not None


def test_unwired_inputs_are_missing_not_fabricated(test_db_session):
    _seed_bars(test_db_session, "AAPL", dt.date(2024, 1, 1), 260)
    dates = _bar_dates(test_db_session, "AAPL")
    pred = dt.datetime.combine(dates[-1], dt.time(23, 59, 59), tzinfo=_UTC)
    provider = SilverPitFeatureProvider(test_db_session)
    feats = {f.feature_name: f for f in provider.get_features("AAPL", pred)}

    # Groups with no wired point-in-time source must be MISSING with no value.
    for name in (
        "pit_revenue_yoy", "macro_cpi_yoy_pit", "trading_days_to_earnings",
        "market_regime", "rule_based_setup_strength", "estimated_spread_bps",
        "sector_return_20d",
    ):
        assert feats[name].data_quality_status is DataQualityStatus.MISSING
        assert feats[name].feature_value is None


def test_no_history_yields_all_missing(test_db_session):
    # A symbol that exists but has no knowable bars at the prediction time.
    _seed_bars(test_db_session, "AAPL", dt.date(2025, 6, 1), 10)
    pred = dt.datetime(2025, 1, 1, tzinfo=_UTC)  # before any bar
    provider = SilverPitFeatureProvider(test_db_session)
    feats = provider.get_features("AAPL", pred)
    non_meta = [f for f in feats if f.feature_name != "feature_completeness"]
    assert non_meta and all(f.data_quality_status is DataQualityStatus.MISSING for f in non_meta)
    completeness = next(f for f in feats if f.feature_name == "feature_completeness")
    assert completeness.feature_value == 0.0
    # Even all-missing features carry complete, non-leaking provenance.
    for f in feats:
        assert validate_feature(f, for_training=True) is None


# --- executable entry / forward path (offline, forward-only) -----------------


def test_executable_entry_is_next_session_open(test_db_session):
    _seed_bars(test_db_session, "AAPL", dt.date(2025, 1, 1), 40)
    dates = _bar_dates(test_db_session, "AAPL")
    pred = dt.datetime.combine(dates[19], dt.time(23, 59, 59), tzinfo=_UTC)
    provider = SilverPitFeatureProvider(test_db_session)
    entry = provider.get_executable_entry("AAPL", pred)
    assert entry is not None
    entry_session, entry_price = entry
    # Strictly after the prediction date, and it's the 21st bar's open.
    assert entry_session.date() == dates[20]
    assert entry_price == pytest.approx((100.0 + 20 * 0.5) - 0.2)


def test_forward_path_is_forward_only_and_bounded(test_db_session):
    _seed_bars(test_db_session, "AAPL", dt.date(2025, 1, 1), 40)
    dates = _bar_dates(test_db_session, "AAPL")
    pred = dt.datetime.combine(dates[19], dt.time(23, 59, 59), tzinfo=_UTC)
    provider = SilverPitFeatureProvider(test_db_session)
    entry_session, _ = provider.get_executable_entry("AAPL", pred)
    path = provider.get_forward_path("AAPL", entry_session, horizon_days=5)
    assert len(path) == 5
    # `session` carries the bar date (the orderable path key).
    assert path[0].session == dates[20]
    assert all(b.session >= dates[20] for b in path)
    assert [b.session for b in path] == sorted(b.session for b in path)


# --- end-to-end: drives the training builder ---------------------------------


def test_provider_drives_training_example_builder(test_db_session):
    _seed_bars(test_db_session, "AAPL", dt.date(2024, 1, 1), 260)
    dates = _bar_dates(test_db_session, "AAPL")
    pred = dt.datetime.combine(dates[220], dt.time(23, 59, 59), tzinfo=_UTC)
    provider = SilverPitFeatureProvider(test_db_session)

    builder = TrainingExampleBuilder(
        provider, for_training=True, is_synthetic=True, source_providers=["yahoo"]
    )
    dataset = builder.build([ExampleRequest("AAPL", pred, direction="long", horizon_days=5)])

    assert dataset.size == 1
    ex = dataset.examples[0]
    assert ex.entry_session.date() == dates[221]
    assert ex.features.get("adj_close") is not None
    assert ex.labels is not None
    # Unwired inputs surface as recorded requirement gaps, not silent zeros.
    assert "macro_cpi_yoy_pit" in ex.requirement_gaps
    assert dataset.is_synthetic is True
