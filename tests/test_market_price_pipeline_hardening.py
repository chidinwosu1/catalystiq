import datetime as dt
from unittest.mock import MagicMock

from catalystiq.analysis import config as analysis_config
from catalystiq.db import models
from catalystiq.pipelines import market_price_pipeline as pipe
from catalystiq.schemas.market_data import OHLCVBar, Quote


def _bars(n=300, close_fn=None):
    """Ends today (walking backward) so FreshnessPolicy considers the
    result fresh - see tests/test_market_price_pipeline.py's _bars()."""
    close_fn = close_fn or (lambda i: 100 + i * 0.25)
    bars = []
    d = dt.date.today()
    while len(bars) < n:
        if d.weekday() < 5:
            close = close_fn(n - 1 - len(bars))
            bars.append(
                OHLCVBar(
                    date=d,
                    open=close - 0.2,
                    high=close + 0.5,
                    low=close - 0.5,
                    close=close,
                    volume=1_000_000 + len(bars),
                )
            )
        d -= dt.timedelta(days=1)
    bars.reverse()
    return bars


def _provider(bars, quote_price=None):
    provider = MagicMock()
    provider.ADAPTER_VERSION = "1.0.0"
    provider.get_ohlcv.return_value = bars
    provider.get_quote.return_value = Quote(
        symbol="X",
        price=quote_price if quote_price is not None else bars[-1].close,
        previous_close=bars[-1].close,
        as_of=dt.datetime.now(dt.timezone.utc),
    )
    return provider


# --- Bronze: source-aligned payload + request metadata --------------------


def test_ingest_bronze_captures_request_metadata(test_db_session):
    provider = _provider(_bars(50))
    run = pipe.ingest_bronze("AAPL", 100, provider, test_db_session, interval="1d")

    assert run.requested_symbol == "AAPL"
    assert run.requested_interval == "1d"
    assert run.requested_start is not None
    assert run.requested_end is not None
    assert run.request_params["days"] == 100
    assert run.provider_adapter_version == "1.0.0"

    row = test_db_session.query(models.BronzeMarketPriceBar).first()
    # Source-aligned (the OHLCVBar shape the provider adapter returned),
    # not byte-for-byte raw Yahoo JSON.
    assert set(row.raw_payload.keys()) == {"date", "open", "high", "low", "close", "volume"}


# --- Silver: multi-run builds ----------------------------------------------


def test_silver_build_spans_multiple_bronze_runs(test_db_session):
    provider = _provider(_bars(300))
    run1 = pipe.ingest_bronze("AAPL", 400, provider, test_db_session)

    provider2 = _provider(_bars(305))
    run2 = pipe.ingest_bronze("AAPL", 400, provider2, test_db_session)

    result = pipe.build_silver("AAPL", test_db_session)  # no explicit run -> spans both

    assert result.upserted == 305
    contributing = (
        test_db_session.query(models.SilverBuildRunBronzeIngestionRun)
        .filter_by(silver_build_run_id=result.silver_build_run.id)
        .all()
    )
    assert {c.bronze_ingestion_run_id for c in contributing} == {run1.id, run2.id}


def test_silver_build_snapshot_is_immutable(test_db_session):
    provider = _provider(_bars(300))
    pipe.ensure_fresh("AAPL", provider, test_db_session)

    build1 = pipe.get_latest_silver_build_run("AAPL", test_db_session)
    snapshot_count = (
        test_db_session.query(models.SilverBuildRunBar).filter_by(silver_build_run_id=build1.id).count()
    )
    assert snapshot_count == 300

    # Mutate the live current-state table directly (simulating a later
    # correction) - the old build's snapshot must be untouched.
    live_row = test_db_session.query(models.SilverPriceBar).first()
    live_row.close = 999.99
    test_db_session.commit()

    snapshot_row = (
        test_db_session.query(models.SilverBuildRunBar)
        .filter_by(silver_build_run_id=build1.id, bar_date=live_row.date)
        .one()
    )
    assert snapshot_row.close != 999.99


# --- Gold: reproducibility + exact build reference --------------------------


def test_old_gold_snapshot_reproducible_after_newer_silver(test_db_session):
    provider = _provider(_bars(300))
    pipe.ensure_fresh("AAPL", provider, test_db_session)
    old_snapshot = pipe.build_gold_technical("AAPL", test_db_session)
    old_build_id = old_snapshot.lineage.silver_build_run_id

    # A brand-new Silver build (distinct from the one Gold was computed
    # against) - e.g. a later re-ingest.
    provider2 = _provider(_bars(305))
    run = pipe.ingest_bronze("AAPL", 400, provider2, test_db_session)
    pipe.build_silver("AAPL", test_db_session, ingestion_run=run)

    new_snapshot = pipe.build_gold_technical("AAPL", test_db_session)
    assert new_snapshot.lineage.silver_build_run_id != old_build_id

    old_row = (
        test_db_session.query(models.TechnicalSnapshotRecord)
        .filter_by(silver_build_run_id=old_build_id)
        .one_or_none()
    )
    assert old_row is not None
    assert old_row.silver_record_count == 300  # frozen at the time it was computed


def test_gold_lineage_references_exact_silver_build(test_db_session):
    provider = _provider(_bars(300))
    pipe.ensure_fresh("AAPL", provider, test_db_session)
    build = pipe.get_latest_silver_build_run("AAPL", test_db_session)

    snapshot = pipe.build_gold_technical("AAPL", test_db_session)

    assert snapshot.lineage.silver_build_run_id == build.id


# --- Configuration versioning ------------------------------------------------


def test_configuration_change_produces_distinguishable_gold_row(test_db_session, monkeypatch):
    provider = _provider(_bars(300))
    pipe.ensure_fresh("AAPL", provider, test_db_session)

    snap_v1 = pipe.build_gold_technical("AAPL", test_db_session)
    rows_before = test_db_session.query(models.TechnicalSnapshotRecord).count()

    changed_config = analysis_config.TechnicalConfig(rsi_period=21)
    monkeypatch.setitem(analysis_config.PRODUCT_CONFIGS, "technical", changed_config)

    snap_v2 = pipe.build_gold_technical("AAPL", test_db_session)
    rows_after = test_db_session.query(models.TechnicalSnapshotRecord).count()

    assert snap_v1.lineage.configuration_version != snap_v2.lineage.configuration_version
    assert rows_after == rows_before + 1  # a new row, not an overwrite


# --- Product selectivity -----------------------------------------------------


def test_build_gold_technical_only_computes_technical(test_db_session):
    provider = _provider(_bars(300))
    pipe.ensure_fresh("AAPL", provider, test_db_session)

    pipe.build_gold_technical("AAPL", test_db_session)

    assert test_db_session.query(models.TechnicalSnapshotRecord).count() == 1
    assert test_db_session.query(models.MarketStructureSnapshotRecord).count() == 0
    assert test_db_session.query(models.RiskSnapshotRecord).count() == 0
    assert test_db_session.query(models.VolumeLiquiditySnapshotRecord).count() == 0
    assert test_db_session.query(models.MarketContextSnapshotRecord).count() == 0


def test_build_gold_dispatcher_computes_only_requested_products(test_db_session):
    provider = _provider(_bars(300))
    pipe.ensure_fresh("AAPL", provider, test_db_session)

    results = pipe.build_gold(
        "AAPL", test_db_session, requested_products={pipe.GoldProduct.RISK, pipe.GoldProduct.VOLUME_LIQUIDITY}
    )

    assert set(results.keys()) == {pipe.GoldProduct.RISK, pipe.GoldProduct.VOLUME_LIQUIDITY}
    assert test_db_session.query(models.TechnicalSnapshotRecord).count() == 0
    assert test_db_session.query(models.RiskSnapshotRecord).count() == 1
    assert test_db_session.query(models.VolumeLiquiditySnapshotRecord).count() == 1


# --- Multi-symbol dependency lineage -----------------------------------------


def test_market_context_records_benchmark_and_sector_dependencies(test_db_session):
    provider = _provider(_bars(300))
    pipe.ensure_fresh("AAPL", provider, test_db_session)
    pipe.ensure_fresh("SPY", provider, test_db_session)
    pipe.ensure_fresh("XLK", provider, test_db_session)

    snapshot = pipe.build_gold_market_context(
        "AAPL", test_db_session, market_symbol="SPY", sector_symbol="XLK"
    )

    roles = {d.role: d.symbol for d in snapshot.lineage.dependencies}
    assert roles == {"primary": "AAPL", "market": "SPY", "sector": "XLK"}

    dep_rows = (
        test_db_session.query(models.GoldCalculationRunDependency)
        .join(models.GoldCalculationRun)
        .filter(models.GoldCalculationRun.product_name == "market_context")
        .all()
    )
    assert {d.role for d in dep_rows} == {"primary", "market", "sector"}


# --- Quote persistence --------------------------------------------------


def test_ingest_bronze_quote_persists_and_returns_quote(test_db_session):
    provider = _provider(_bars(50), quote_price=123.45)
    run = pipe.ingest_bronze("AAPL", 100, provider, test_db_session)

    quote = pipe.ingest_bronze_quote("AAPL", provider, test_db_session, ingestion_run=run)

    assert quote is not None
    assert quote.price == 123.45
    row = test_db_session.query(models.BronzeMarketQuote).filter_by(ingestion_run_id=run.id).one()
    assert row.price == 123.45
    assert row.raw_payload["price"] == 123.45


def test_ingest_bronze_quote_failure_returns_none_without_raising(test_db_session):
    from catalystiq.providers.market_data import MarketDataError

    provider = MagicMock()
    provider.get_quote.side_effect = MarketDataError("quote service down")

    result = pipe.ingest_bronze_quote("AAPL", provider, test_db_session)

    assert result is None
    assert test_db_session.query(models.BronzeMarketQuote).count() == 0


def test_ensure_fresh_persists_quote_on_ingest(test_db_session):
    provider = _provider(_bars(300), quote_price=555.0)

    pipe.ensure_fresh("AAPL", provider, test_db_session)

    assert test_db_session.query(models.BronzeMarketQuote).count() == 1
    build = pipe.get_latest_silver_build_run("AAPL", test_db_session)
    assert build.quote_used is True


# --- Transaction / partial-failure boundaries -------------------------------


def test_bronze_ingestion_failure_leaves_run_failed_and_no_bars(test_db_session):
    from catalystiq.providers.market_data import MarketDataError

    provider = MagicMock()
    provider.ADAPTER_VERSION = "1.0.0"
    provider.get_ohlcv.side_effect = MarketDataError("provider down")

    try:
        pipe.ingest_bronze("AAPL", 100, provider, test_db_session)
    except MarketDataError:
        pass

    run = test_db_session.query(models.BronzeIngestionRun).one()
    assert run.status == "failed"
    assert run.error_detail is not None
    assert test_db_session.query(models.BronzeMarketPriceBar).count() == 0


def test_silver_build_marks_partial_on_malformed_bronze_row(test_db_session):
    provider = _provider(_bars(50))
    run = pipe.ingest_bronze("AAPL", 100, provider, test_db_session)

    # Corrupt one Bronze row's payload so it can't parse back into an
    # OHLCVBar.
    bad_row = test_db_session.query(models.BronzeMarketPriceBar).first()
    bad_row.raw_payload = {"not": "a valid bar"}
    test_db_session.commit()

    result = pipe.build_silver("AAPL", test_db_session, ingestion_run=run)

    assert result.silver_build_run.status == "partial"
    assert result.upserted == 49


def test_silver_failure_does_not_corrupt_bronze(test_db_session):
    """A Silver build failure shouldn't roll back or affect the Bronze run
    that already succeeded."""
    provider = _provider(_bars(50))
    run = pipe.ingest_bronze("AAPL", 100, provider, test_db_session)
    assert run.status == "succeeded"
    bronze_count_before = test_db_session.query(models.BronzeMarketPriceBar).count()

    # Every row malformed -> build_silver has nothing usable -> "failed".
    for row in test_db_session.query(models.BronzeMarketPriceBar).filter_by(ingestion_run_id=run.id):
        row.raw_payload = {"not": "valid"}
    test_db_session.commit()

    result = pipe.build_silver("AAPL", test_db_session, ingestion_run=run)

    assert result.silver_build_run.status == "failed"
    # Bronze rows (still holding the corrupted payload we wrote above, but
    # the ROW COUNT/run status are untouched by the Silver failure) remain.
    assert test_db_session.query(models.BronzeMarketPriceBar).count() == bronze_count_before
    assert test_db_session.query(models.BronzeIngestionRun).one().status == "succeeded"


def test_gold_failure_does_not_corrupt_silver_or_other_products(test_db_session, monkeypatch):
    provider = _provider(_bars(300))
    pipe.ensure_fresh("AAPL", provider, test_db_session)
    silver_count_before = test_db_session.query(models.SilverPriceBar).count()

    def _boom(*args, **kwargs):
        raise RuntimeError("compute blew up")

    monkeypatch.setattr(pipe, "compute_technical_snapshot", _boom)

    try:
        pipe.build_gold_technical("AAPL", test_db_session)
    except RuntimeError:
        pass

    run = (
        test_db_session.query(models.GoldCalculationRun)
        .filter_by(product_name="technical")
        .order_by(models.GoldCalculationRun.id.desc())
        .first()
    )
    assert run.status == "failed"
    assert test_db_session.query(models.SilverPriceBar).count() == silver_count_before

    # A different product's Gold calculation still works fine afterward.
    snapshot = pipe.build_gold_volume_liquidity("AAPL", test_db_session)
    assert snapshot is not None
