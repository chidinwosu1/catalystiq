"""Dry-run CLI wiring (no network): fail-closed + JSON report."""
import datetime as dt
import json
import math

import pytest

from catalystiq.db import models
from catalystiq.ml.dry_run_cli import _prediction_dates, main
from catalystiq.ml.flags import MLDisabledError


def _seed(db, sym, *, n=280, seed=1.0, drift=0.0006):
    t = models.Ticker(symbol=sym.upper(), sector="Technology")
    db.add(t)
    db.flush()
    base = dt.date(2019, 1, 1)
    p = 100.0 * seed
    now = dt.datetime(2019, 1, 1)
    for i in range(n):
        p *= 1 + drift + 0.01 * math.sin(i / 7)
        db.add(models.SilverPriceBar(
            ticker_id=t.id, date=base + dt.timedelta(days=i),
            open=p * 0.995, high=p * 1.015, low=p * 0.985, close=p,
            volume=1_000_000 + i, data_quality_status="ok", created_at=now, updated_at=now))
    db.flush()


def test_prediction_dates_weekly():
    dates = _prediction_dates("2020-01-01", "2020-01-29", 7)
    assert len(dates) == 5
    assert all(d.hour == 20 for d in dates)


def test_cli_fails_closed_without_enable(test_db_session):
    _seed(test_db_session, "AAA")
    _seed(test_db_session, "SPY", seed=4.0)
    with pytest.raises(MLDisabledError):
        main(
            ["--symbols", "AAA", "--start", "2019-08-05", "--end", "2019-09-02",
             "--horizon", "5"],  # no --enable
            db=test_db_session,
        )


def test_cli_runs_and_prints_json_report(test_db_session, capsys):
    for s, sd, dr in [("AAA", 1.0, 0.0007), ("BBB", 2.0, -0.0003), ("SPY", 4.0, 0.0004)]:
        _seed(test_db_session, s, seed=sd, drift=dr)
    code = main(
        ["--symbols", "AAA,BBB", "--benchmark", "SPY",
         "--start", "2019-08-05", "--end", "2019-09-16", "--step-days", "7",
         "--horizon", "5", "--enable", "--min-examples-to-fit", "10000"],
        db=test_db_session,
    )
    out = capsys.readouterr().out
    report = json.loads(out)
    assert report["dataset_size"] > 0
    assert report["folds"]["chronology_ok"] is True
    assert report["folds"]["leakage_findings"] == []
    assert "sufficient_for_training" in report["sufficiency"]
    assert report["ingest_warnings"] == []  # --ingest not passed
    # exit code reflects the sufficiency verdict (few examples here -> not sufficient)
    assert code in (0, 1)
