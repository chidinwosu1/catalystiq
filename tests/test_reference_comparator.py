"""End-to-end tests for the reference-calculation comparator
(catalystiq/validation/reference/comparator.py): a clean Gold calculation
should pass every applicable check, an injected mismatch should fail and
produce a complete audit row, and a fail must quarantine the Gold output
rather than silently overwriting it."""
import datetime as dt
from unittest.mock import MagicMock

import catalystiq.validation.reference.comparator as comparator_module
from catalystiq.db import models
from catalystiq.pipelines import market_price_pipeline as pipe
from catalystiq.schemas.market_data import OHLCVBar, Quote
from catalystiq.validation.reference.comparator import run_reference_check


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


def _latest_run(db, product_name, calculation_version=pipe.DEFAULT_CALCULATION_VERSION):
    return (
        db.query(models.GoldCalculationRun)
        .filter_by(product_name=product_name, calculation_version=calculation_version)
        .order_by(models.GoldCalculationRun.id.desc())
        .first()
    )


def test_technical_checks_all_pass_on_clean_data(test_db_session):
    pipe.ensure_fresh("AAPL", _provider(_bars(300, seed=3)), test_db_session)
    pipe.build_gold_technical("AAPL", test_db_session)

    run = _latest_run(test_db_session, "technical")
    checks = run_reference_check(run.id, test_db_session)

    assert len(checks) > 0
    assert all(c.status in ("pass", "not_applicable") for c in checks)
    assert any(c.status == "pass" for c in checks)


def test_volume_liquidity_checks_all_pass_on_clean_data(test_db_session):
    pipe.ensure_fresh("AAPL", _provider(_bars(300, seed=4)), test_db_session)
    pipe.build_gold_volume_liquidity("AAPL", test_db_session)

    run = _latest_run(test_db_session, "volume_liquidity")
    checks = run_reference_check(run.id, test_db_session)

    assert len(checks) == 5
    assert all(c.status == "pass" for c in checks)


def test_risk_checks_all_pass_with_benchmark(test_db_session):
    provider = _provider(_bars(300, seed=5))
    pipe.ensure_fresh("AAPL", provider, test_db_session)
    pipe.ensure_fresh("SPY", _provider(_bars(300, seed=6)), test_db_session)
    pipe.build_gold_risk("AAPL", test_db_session, benchmark_symbol="SPY")

    run = _latest_run(test_db_session, "risk")
    checks = run_reference_check(run.id, test_db_session)

    assert len(checks) == 6
    assert all(c.status == "pass" for c in checks)


def test_market_structure_pivot_checks_pass(test_db_session):
    pipe.ensure_fresh("AAPL", _provider(_bars(300, seed=7)), test_db_session)
    pipe.build_gold_market_structure("AAPL", test_db_session)

    run = _latest_run(test_db_session, "market_structure")
    checks = run_reference_check(run.id, test_db_session)

    assert len(checks) == 2
    assert all(c.status == "pass" for c in checks)


def test_injected_mismatch_fails_and_records_full_audit_row(test_db_session, monkeypatch):
    pipe.ensure_fresh("AAPL", _provider(_bars(300, seed=8)), test_db_session)
    pipe.build_gold_technical("AAPL", test_db_session, calculation_version="9.9.9")
    run = _latest_run(test_db_session, "technical", calculation_version="9.9.9")

    orig_compare = comparator_module._compare

    def bad_compare(run, name, source, expected, actual, *args, **kwargs):
        if name == "rsi_14":
            actual = (actual or 0) + 9999.0
        return orig_compare(run, name, source, expected, actual, *args, **kwargs)

    monkeypatch.setattr(comparator_module, "_compare", bad_compare)

    checks = run_reference_check(run.id, test_db_session)

    rsi_check = next(c for c in checks if c.indicator_name == "rsi_14")
    assert rsi_check.status == "fail"
    assert rsi_check.discrepancy_reason is not None
    assert rsi_check.reference_source == "talib"
    assert rsi_check.reference_library == "TA-Lib"
    assert rsi_check.expected_value is not None
    assert rsi_check.actual_value is not None
    assert rsi_check.tolerance_abs is not None
    assert rsi_check.checked_at is not None


def test_failed_check_quarantines_gold_row_without_overwriting_payload(test_db_session, monkeypatch):
    pipe.ensure_fresh("AAPL", _provider(_bars(300, seed=9)), test_db_session)
    snapshot = pipe.build_gold_technical("AAPL", test_db_session, calculation_version="8.8.8")
    run = _latest_run(test_db_session, "technical", calculation_version="8.8.8")

    gold_row_before = (
        test_db_session.query(models.TechnicalSnapshotRecord).filter_by(gold_calculation_run_id=run.id).one()
    )
    payload_before = dict(gold_row_before.payload)
    assert gold_row_before.data_quality_status == "available"

    orig_compare = comparator_module._compare

    def bad_compare(run, name, source, expected, actual, *args, **kwargs):
        if name == "sma_20":
            actual = (actual or 0) + 9999.0
        return orig_compare(run, name, source, expected, actual, *args, **kwargs)

    monkeypatch.setattr(comparator_module, "_compare", bad_compare)
    run_reference_check(run.id, test_db_session)

    gold_row_after = (
        test_db_session.query(models.TechnicalSnapshotRecord).filter_by(gold_calculation_run_id=run.id).one()
    )
    assert gold_row_after.data_quality_status == "quarantined"
    assert gold_row_after.payload == payload_before  # never overwritten


def test_run_reference_check_noops_without_silver_build(test_db_session):
    run = models.GoldCalculationRun(
        ticker_id=1,
        product_name="technical",
        calculation_version="1.0.0",
        configuration_version="abc",
        configuration_snapshot={},
        silver_build_run_id=None,
        status="succeeded",
        started_at=dt.datetime.utcnow(),
        created_at=dt.datetime.utcnow(),
    )
    test_db_session.add(run)
    test_db_session.commit()

    checks = run_reference_check(run.id, test_db_session)

    assert checks == []
