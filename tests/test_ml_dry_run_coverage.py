"""Regression tests for the dry-run coverage/as-of/leakage bug.

Reproduces and locks the fix for the real-data failure where requesting
prediction dates OUTSIDE the ingested Silver window silently produced
degenerate examples (all-missing features, 0 completeness, 0 folds, 100%
positive labels) by pulling the entry from a disconnected FUTURE bar.

Covers: historical date-range ingestion (days computation), as-of matching,
feature completeness > 0 on covered data, and non-degenerate labels.
"""
import datetime as dt
import math

import pytest

from catalystiq.config import Settings
from catalystiq.db import models
from catalystiq.ml.dataset.builder import ExampleRequest, TrainingExampleBuilder
from catalystiq.ml.dry_run import run_training_dry_run
from catalystiq.ml.features.pit_provider import (
    MAX_ENTRY_GAP_DAYS,
    SilverPointInTimeProvider,
)


def _weekdays(start: dt.date, n: int) -> list[dt.date]:
    """`n` successive weekday (Mon-Fri) dates from `start` - a realistic
    trading-day calendar (holidays ignored; good enough for tests)."""
    out: list[dt.date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def _seed(db, sym, *, start: dt.date, n: int, seed=1.0, drift=0.0002, osc=0.012):
    t = models.Ticker(symbol=sym.upper(), sector="Technology")
    db.add(t)
    db.flush()
    p = 100.0 * seed
    now = dt.datetime(2019, 1, 1)
    for i, d in enumerate(_weekdays(start, n)):
        p *= 1 + drift + osc * math.sin(i / 5)  # oscillation -> mixed-sign fwd returns
        db.add(models.SilverPriceBar(
            ticker_id=t.id, date=d,
            open=p * 0.995, high=p * 1.015, low=p * 0.985, close=p,
            volume=1_000_000 + i, data_quality_status="ok", created_at=now, updated_at=now))
    db.flush()


def _provider(db):
    return SilverPointInTimeProvider(db, benchmark_symbol="SPY", sector_resolver=lambda s: None)


def _enabling():
    return Settings(action_api_key="k", enable_ml=True, enable_ml_training=True)


# --- as-of matching / fail-closed on uncovered history ----------------------
def test_uncovered_dates_produce_no_examples_not_degenerate(test_db_session):
    # Silver only exists in 2023; requested prediction dates are in 2020.
    _seed(test_db_session, "AAA", start=dt.date(2023, 1, 1), n=300)
    _seed(test_db_session, "SPY", start=dt.date(2023, 1, 1), n=300, seed=4.0)
    reqs = [ExampleRequest("AAA", dt.datetime(2020, 6, 1, 20) + dt.timedelta(days=7 * i), "long", 5)
            for i in range(12)]
    ds = TrainingExampleBuilder(_provider(test_db_session), is_synthetic=True).build(reqs)
    # NO degenerate examples built; every request skipped with a coverage reason.
    assert ds.size == 0
    assert len(ds.skipped) == 12
    assert all("point-in-time" in s["reason"] or "as-of" in s["reason"] for s in ds.skipped)


def test_executable_entry_requires_history_and_contiguity(test_db_session):
    _seed(test_db_session, "AAA", start=dt.date(2020, 1, 1), n=200)  # ~Jan-Oct 2020 weekdays
    prov = _provider(test_db_session)
    pol = prov.freshness_policy
    # (a) prediction BEFORE all history -> None (no as-of bar)
    assert prov.get_executable_entry("AAA", dt.datetime(2019, 6, 1, 22)) is None
    # (b) prediction within the covered series -> the first session strictly
    #     after the last COMPLETE session (per the trading calendar).
    pred = dt.datetime(2020, 3, 16, 22)
    entry = prov.get_executable_entry("AAA", pred)
    assert entry is not None
    last_closed = pol.latest_expected_session(pred)
    assert entry[0].date() > last_closed
    assert (entry[0].date() - last_closed).days <= MAX_ENTRY_GAP_DAYS
    # (c) prediction AFTER all history -> None (live-inference edge, no next bar)
    assert prov.get_executable_entry("AAA", dt.datetime(2021, 6, 1, 22)) is None


def test_gap_after_last_closed_is_refused(test_db_session):
    # A symbol that stops trading: a January 2020 block (history), then the next
    # bar is months later. A prediction in the gap must be refused - entering at
    # the distant bar would leak future information.
    t = models.Ticker(symbol="GAP", sector="X")
    test_db_session.add(t)
    test_db_session.flush()
    now = dt.datetime(2019, 1, 1)
    dates = _weekdays(dt.date(2020, 1, 1), 20) + [dt.date(2020, 9, 1)]
    for i, d in enumerate(dates):
        test_db_session.add(models.SilverPriceBar(
            ticker_id=t.id, date=d, open=100 + i, high=101 + i, low=99 + i, close=100 + i,
            volume=1_000_000, data_quality_status="ok", created_at=now, updated_at=now))
    test_db_session.flush()
    prov = _provider(test_db_session)
    # Prediction 2020-04-06: history exists (January block) but the next bar is
    # 2020-09-01 (a ~5-month gap) -> refused.
    assert prov.get_executable_entry("GAP", dt.datetime(2020, 4, 6, 22)) is None


# --- covered data: completeness > 0, folds > 0, non-degenerate labels -------
def test_covered_dates_yield_real_features_folds_and_mixed_labels(test_db_session):
    # ~18 months of history from 2019-05 so 2020 prediction dates have warm-up.
    _seed(test_db_session, "AAA", start=dt.date(2019, 5, 1), n=560, seed=1.0)
    _seed(test_db_session, "SPY", start=dt.date(2019, 5, 1), n=560, seed=4.0, drift=0.0003)
    dates = [dt.datetime(2020, 1, 6, 20) + dt.timedelta(days=7 * i) for i in range(30)]
    report = run_training_dry_run(
        test_db_session, symbols=["AAA"], prediction_timestamps=dates,
        horizon_days=5, is_synthetic_data=True, settings=_enabling(),
        min_examples_to_fit=10_000,  # skip the fit; we assert the pipeline shape
    )
    assert report.dataset_size > 0
    # completeness is NOT silently zero
    assert report.coverage.mean_completeness > 0.0
    assert report.coverage.per_group_present_rate["adjusted_ohlcv"] > 0.9
    assert report.coverage.per_group_present_rate["rsi_macd"] > 0.9
    # chronological folds actually form (outcome windows no longer land years
    # after the prediction, so purge doesn't wipe everything)
    assert report.folds.n_folds >= 1
    assert report.folds.chronology_ok is True
    assert report.folds.leakage_findings == []
    # labels are non-degenerate (not 100%/0% positive)
    pr = report.labels.net_profit_positive_rate
    assert 0.0 < pr < 1.0


# --- historical date-range ingestion (days computation, no network) ---------
def test_ingest_fetches_days_covering_requested_start(monkeypatch):
    import catalystiq.pipelines.market_price_pipeline as mpp
    import catalystiq.providers.market_data as md
    from catalystiq.ml import dry_run_cli

    captured: dict = {}

    def fake_ensure_fresh(sym, provider, db, days=None, **kw):
        captured.setdefault("days", days)
        captured.setdefault("symbols", []).append(sym)

    monkeypatch.setattr(mpp, "ensure_fresh", fake_ensure_fresh)
    monkeypatch.setattr(md, "get_market_data_provider", lambda: object())

    start = dt.date(2020, 1, 1)
    warnings = dry_run_cli._ingest(["AAA", "MSFT"], "SPY", db=None, start=start)
    assert warnings == []
    # days must reach back to at least start minus the indicator warm-up
    needed = (dt.date.today() - start).days + dry_run_cli.INDICATOR_WARMUP_DAYS
    assert captured["days"] >= needed
    assert "SPY" in captured["symbols"]  # benchmark included
