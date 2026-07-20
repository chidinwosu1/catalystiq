"""Long-history validation orchestrator: fail-closed + multi-horizon build."""
import datetime as dt

import pytest

from catalystiq.config import Settings
from catalystiq.db import models
from catalystiq.ml.flags import MLDisabledError
from catalystiq.ml.validate_history import main, run_history_validation


def _weekdays(start: dt.date, end: dt.date):
    d, out = start, []
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def _seed(db, sym, *, start: dt.date, end: dt.date, seed=1.0):
    import math
    t = models.Ticker(symbol=sym.upper(), sector="Technology")
    db.add(t)
    db.flush()
    now = dt.datetime(2019, 1, 1)
    p = 100.0 * seed
    for i, d in enumerate(_weekdays(start, end)):
        p *= 1 + 0.0002 + 0.012 * math.sin(i / 5)
        db.add(models.SilverPriceBar(
            ticker_id=t.id, date=d, open=p * 0.995, high=p * 1.015, low=p * 0.985, close=p,
            volume=1_000_000 + i, data_quality_status="ok", created_at=now, updated_at=now))
    db.flush()


def _enabling():
    return Settings(action_api_key="k", enable_ml=True, enable_ml_training=True)


def test_fails_closed_when_training_disabled(test_db_session):
    with pytest.raises(MLDisabledError):
        run_history_validation(
            test_db_session, symbols=["AAA"], benchmark="SPY",
            start=dt.date(2020, 1, 1), end=dt.date(2020, 6, 30), horizons=[1, 5],
            settings=Settings(action_api_key="k"),  # disabled
        )


def test_incomplete_history_fails_closed_before_model_work(test_db_session):
    # Silver only exists in 2023; requested range is 2020 -> incomplete.
    _seed(test_db_session, "AAA", start=dt.date(2023, 1, 1), end=dt.date(2023, 12, 31))
    _seed(test_db_session, "SPY", start=dt.date(2023, 1, 1), end=dt.date(2023, 12, 31), seed=4.0)
    report = run_history_validation(
        test_db_session, symbols=["AAA"], benchmark="SPY",
        start=dt.date(2020, 1, 1), end=dt.date(2020, 6, 30), horizons=[1, 5, 10, 20],
        settings=_enabling(),
    )
    assert report["status"] == "incomplete_history"
    assert "AAA" in report["incomplete_symbols"]
    # fail closed BEFORE any model work
    assert "horizons_results" not in report
    # per-symbol audit is still reported
    assert report["symbol_coverage"]["AAA"]["complete"] is False


def test_complete_history_runs_multi_horizon_reusing_features(test_db_session):
    # Fully cover a short requested window (+ warm-up before, + forward past end).
    _seed(test_db_session, "AAA", start=dt.date(2019, 1, 1), end=dt.date(2020, 6, 30))
    _seed(test_db_session, "SPY", start=dt.date(2019, 1, 1), end=dt.date(2020, 6, 30), seed=4.0)
    report = run_history_validation(
        test_db_session, symbols=["AAA"], benchmark="SPY",
        start=dt.date(2020, 1, 6), end=dt.date(2020, 3, 6), horizons=[1, 5],
        step_days=7, settings=_enabling(), is_synthetic_data=True,
    )
    assert report["status"] == "ok"
    assert report["symbol_coverage"]["AAA"]["complete"] is True
    # multi-horizon: both horizons present, sharing the same usable example set
    assert set(report["horizons_results"]) == {"1", "5"}
    assert report["per_symbol_examples"]["AAA"]["usable"] > 0
    assert report["horizons_results"]["1"]["dataset_size"] == report["horizons_results"]["5"]["dataset_size"]
    # feature coverage is bucketed by year and shows real (non-zero) coverage
    fc = report["feature_coverage_by_period"]
    assert "2020" in fc and fc["2020"]["price_present_rate"] > 0.9
    # leakage-free
    for h in ("1", "5"):
        assert report["horizons_results"][h]["folds"]["leakage_findings"] == []


def test_cli_exit_code_incomplete_history(test_db_session):
    _seed(test_db_session, "AAA", start=dt.date(2023, 1, 1), end=dt.date(2023, 6, 30))
    _seed(test_db_session, "SPY", start=dt.date(2023, 1, 1), end=dt.date(2023, 6, 30), seed=4.0)
    code = main(
        ["--symbols", "AAA", "--benchmark", "SPY", "--start", "2020-01-01",
         "--end", "2020-06-30", "--horizons", "1,5", "--enable"],
        db=test_db_session,
    )
    assert code == 2  # incomplete_history
