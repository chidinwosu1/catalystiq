"""Tests for the reference-validation async cycle
(catalystiq/validation/reference/scheduler.py): flagged runs are always
processed, unflagged/unchecked runs are only sampled, and already-checked
runs are never reprocessed."""
import datetime as dt
from unittest.mock import MagicMock

from catalystiq.db import models
from catalystiq.pipelines import market_price_pipeline as pipe
from catalystiq.schemas.market_data import OHLCVBar, Quote
from catalystiq.validation.reference.scheduler import run_reference_validation_cycle


def _bars(n=300, seed=1):
    import numpy as np

    rng = np.random.RandomState(seed)
    bars = []
    price = 100.0
    d = dt.date.today()
    count = 0
    while count < n:
        if d.weekday() < 5:
            price *= 1 + rng.normal(0, 0.01)
            o = price * (1 + rng.normal(0, 0.002))
            c = price
            h = max(o, c) * (1 + abs(rng.normal(0, 0.003)))
            low = min(o, c) * (1 - abs(rng.normal(0, 0.003)))
            v = int(1_000_000 * (1 + abs(rng.normal(0, 0.3))))
            bars.append(OHLCVBar(date=d, open=o, high=h, low=low, close=c, volume=v))
            count += 1
        d -= dt.timedelta(days=1)
    bars.reverse()
    return bars


def _provider(bars):
    provider = MagicMock()
    provider.ADAPTER_VERSION = "1.0.0"
    provider.get_ohlcv.return_value = bars
    provider.get_quote.return_value = Quote(
        symbol="X", price=bars[-1].close, previous_close=bars[-1].close, as_of=dt.datetime.now(dt.timezone.utc)
    )
    return provider


def _gold_run(db, symbol, seed, calculation_version):
    pipe.ensure_fresh(symbol, _provider(_bars(300, seed=seed)), db)
    pipe.build_gold_technical(symbol, db, calculation_version=calculation_version)
    return (
        db.query(models.GoldCalculationRun)
        .filter_by(product_name="technical", calculation_version=calculation_version)
        .order_by(models.GoldCalculationRun.id.desc())
        .first()
    )


def test_flagged_runs_are_always_processed_regardless_of_sample_rate(test_db_session):
    run = _gold_run(test_db_session, "AAPL", seed=11, calculation_version="s1.0.0")
    run.flagged_for_reference_check = True
    test_db_session.commit()

    processed = run_reference_validation_cycle(test_db_session, sample_rate=0.0)

    assert run.id in processed
    test_db_session.refresh(run)
    assert run.flagged_for_reference_check is False
    assert run.reference_checked_at is not None


def test_unflagged_unchecked_runs_are_sampled_at_full_rate(test_db_session):
    run = _gold_run(test_db_session, "AAPL", seed=12, calculation_version="s2.0.0")
    assert run.flagged_for_reference_check is False
    assert run.reference_checked_at is None

    processed = run_reference_validation_cycle(test_db_session, sample_rate=1.0)

    assert run.id in processed
    test_db_session.refresh(run)
    assert run.reference_checked_at is not None


def test_zero_sample_rate_skips_unflagged_runs(test_db_session):
    run = _gold_run(test_db_session, "AAPL", seed=13, calculation_version="s3.0.0")

    processed = run_reference_validation_cycle(test_db_session, sample_rate=0.0)

    assert processed == []
    test_db_session.refresh(run)
    assert run.reference_checked_at is None


def test_already_checked_runs_are_never_reprocessed(test_db_session):
    run = _gold_run(test_db_session, "AAPL", seed=14, calculation_version="s4.0.0")
    run.reference_checked_at = dt.datetime.utcnow()
    test_db_session.commit()

    processed = run_reference_validation_cycle(test_db_session, sample_rate=1.0)

    assert processed == []


def test_failed_runs_are_never_sampled(test_db_session):
    run = _gold_run(test_db_session, "AAPL", seed=15, calculation_version="s5.0.0")
    run.status = "failed"
    test_db_session.commit()

    processed = run_reference_validation_cycle(test_db_session, sample_rate=1.0)

    assert processed == []
