"""train_cli wiring (no network, no live MLflow): fail-closed + JSON report.

The CLI runs the provider path over seeded Silver. With a small seeded window
the sufficiency gate refuses training (no models fitted) - which is exactly the
honest behavior we assert: it does not fabricate a passing result. MLflow is
absent here, so the runner uses the on-disk RecordingTracker fallback.
"""
import datetime as dt
import json
import math

import pytest

from catalystiq.db import models
from catalystiq.ml.flags import MLDisabledError
from catalystiq.ml.train_cli import _offline_settings, main


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


def test_offline_settings_enable_only_this_process():
    s = _offline_settings(True, None, "exp-x")
    assert s.enable_ml is True and s.enable_ml_training is True
    # authorization is training-only; serving/inference/ranking stay off
    assert s.enable_ml_inference is False and s.enable_ml_ranking is False
    assert s.mlflow_experiment_name == "exp-x"
    disabled = _offline_settings(False, None, None)
    assert disabled.enable_ml is False and disabled.enable_ml_training is False


def test_cli_fails_closed_without_enable(test_db_session):
    _seed(test_db_session, "AAA")
    _seed(test_db_session, "SPY", seed=4.0)
    with pytest.raises(MLDisabledError):
        main(
            ["--symbols", "AAA", "--start", "2019-08-05", "--end", "2019-09-02",
             "--horizons", "5"],  # no --enable-training
            db=test_db_session,
        )


def test_cli_runs_and_prints_experiment_json(test_db_session, capsys, tmp_path, monkeypatch):
    # Isolate MLflow tracking to a temp dir so the test never writes ./mlruns.
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "file:" + str(tmp_path / "mlruns"))
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    for s, sd, dr in [("AAA", 1.0, 0.0007), ("BBB", 2.0, -0.0003), ("SPY", 4.0, 0.0004)]:
        _seed(test_db_session, s, seed=sd, drift=dr)
    code = main(
        ["--symbols", "AAA,BBB", "--benchmark", "SPY",
         "--start", "2019-08-05", "--end", "2019-09-16", "--step-days", "7",
         "--horizons", "5", "--enable-training", "--output-dir", str(tmp_path)],
        db=test_db_session,
    )
    report = json.loads(capsys.readouterr().out)
    assert report["horizons"] == [5]
    assert report["feature_schema_version"]
    assert report["horizons_results"], "expected a per-horizon result block"
    hz = report["horizons_results"][0]
    assert "gate" in hz and "sufficiency" in hz["gate"]
    assert report["ingest_warnings"] == []  # --ingest not passed
    # small seeded window -> insufficient -> nothing trained -> non-zero exit
    assert code == 1
