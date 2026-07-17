import datetime as dt

import pytest

from catalystiq.db import models
from catalystiq.pipelines import market_price_pipeline as pipe
from catalystiq.providers.market_data import MarketDataError
from catalystiq.schemas.market_data import OHLCVBar, Quote


def _bars(n=300, start=None, close_fn=None):
    """Defaults to a series ending TODAY (walking backward) so
    FreshnessPolicy - which compares against the most recent completed
    exchange session - considers the result fresh unless a test
    deliberately wants otherwise. Pass an explicit `start` for tests that
    don't care about freshness (e.g. quarantine/validation-only tests)."""
    close_fn = close_fn or (lambda i: 100 + i * 0.25)
    bars = []
    if start is not None:
        d = start
        while len(bars) < n:
            if d.weekday() < 5:
                close = close_fn(len(bars))
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
            d += dt.timedelta(days=1)
        return bars

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


class _FakeProvider:
    def __init__(self, bars, quote=None, fail=False):
        self._bars = bars
        self._quote = quote
        self.fail = fail
        self.calls = 0

    def get_ohlcv(self, symbol, start, end=None, interval="1d"):
        self.calls += 1
        if self.fail:
            raise MarketDataError("provider unavailable")
        return self._bars

    def get_quote(self, symbol):
        if self._quote is not None:
            return self._quote
        latest = self._bars[-1]
        return Quote(
            symbol=symbol.upper(),
            price=latest.close,
            previous_close=latest.close,
            as_of=dt.datetime.now(dt.timezone.utc),
        )

    def get_fundamentals(self, symbol):
        raise NotImplementedError

    def get_news(self, symbol, limit=10):
        raise NotImplementedError


# --- ingest_bronze -----------------------------------------------------


def test_ingest_bronze_writes_run_and_bars(test_db_session):
    bars = _bars(50)
    provider = _FakeProvider(bars)

    run = pipe.ingest_bronze("AAPL", 100, provider, test_db_session)

    assert run.status == "succeeded"
    assert run.bars_fetched == 50
    stored = (
        test_db_session.query(models.BronzeMarketPriceBar)
        .filter_by(ingestion_run_id=run.id)
        .all()
    )
    assert len(stored) == 50
    assert stored[0].raw_payload["close"] == bars[0].close


def test_ingest_bronze_is_additive_across_runs(test_db_session):
    bars = _bars(50)
    provider = _FakeProvider(bars)

    run1 = pipe.ingest_bronze("AAPL", 100, provider, test_db_session)
    run2 = pipe.ingest_bronze("AAPL", 100, provider, test_db_session)

    assert run1.id != run2.id
    total = test_db_session.query(models.BronzeMarketPriceBar).count()
    assert total == 100

    run1_rows = (
        test_db_session.query(models.BronzeMarketPriceBar).filter_by(ingestion_run_id=run1.id).count()
    )
    assert run1_rows == 50


def test_ingest_bronze_marks_run_failed_on_provider_error(test_db_session):
    provider = _FakeProvider([], fail=True)

    with pytest.raises(MarketDataError):
        pipe.ingest_bronze("AAPL", 100, provider, test_db_session)

    run = test_db_session.query(models.BronzeIngestionRun).one()
    assert run.status == "failed"
    assert run.error_detail is not None
    assert test_db_session.query(models.BronzeMarketPriceBar).count() == 0


# --- build_silver --------------------------------------------------------


def test_build_silver_upserts_clean_bars(test_db_session):
    bars = _bars(300)
    provider = _FakeProvider(bars)
    run = pipe.ingest_bronze("AAPL", 400, provider, test_db_session)

    result = pipe.build_silver("AAPL", test_db_session, ingestion_run=run)

    assert result.upserted == 300
    assert result.rejected == 0
    silver_rows = test_db_session.query(models.SilverPriceBar).count()
    assert silver_rows == 300
    row = test_db_session.query(models.SilverPriceBar).order_by(models.SilverPriceBar.date).first()
    assert row.data_quality_status == "clean"
    assert row.source_bronze_ingestion_run_id == run.id


def test_build_silver_is_idempotent(test_db_session):
    bars = _bars(300)
    provider = _FakeProvider(bars)
    run = pipe.ingest_bronze("AAPL", 400, provider, test_db_session)

    pipe.build_silver("AAPL", test_db_session, ingestion_run=run)
    pipe.build_silver("AAPL", test_db_session, ingestion_run=run)

    assert test_db_session.query(models.SilverPriceBar).count() == 300


def test_build_silver_quarantines_invalid_ohlc_bar(test_db_session):
    bars = _bars(300)
    # Corrupt one bar's OHLC relationship (low > high) - must be rejected,
    # not just flagged.
    bad = bars[10]
    bars[10] = OHLCVBar(
        date=bad.date, open=bad.open, high=bad.low - 1, low=bad.low, close=bad.close, volume=bad.volume
    )
    provider = _FakeProvider(bars)
    run = pipe.ingest_bronze("AAPL", 400, provider, test_db_session)

    result = pipe.build_silver("AAPL", test_db_session, ingestion_run=run)

    assert result.rejected == 1
    assert result.upserted == 299
    rejected_rows = test_db_session.query(models.SilverPriceBarRejected).all()
    assert len(rejected_rows) == 1
    assert rejected_rows[0].bar_date == bars[10].date


def test_build_silver_flags_but_keeps_abnormal_gap_bar(test_db_session):
    bars = _bars(300)
    spiked = bars[150]
    bars[150] = OHLCVBar(
        date=spiked.date,
        open=spiked.close * 3,
        high=spiked.close * 3 + 1,
        low=spiked.close * 3 - 1,
        close=spiked.close * 3,
        volume=spiked.volume,
    )
    provider = _FakeProvider(bars)
    run = pipe.ingest_bronze("AAPL", 400, provider, test_db_session)

    result = pipe.build_silver("AAPL", test_db_session, ingestion_run=run)

    assert result.upserted == 300
    assert result.rejected == 0
    flagged = (
        test_db_session.query(models.SilverPriceBar)
        .filter_by(data_quality_status="clean_with_warnings")
        .all()
    )
    assert len(flagged) >= 1
    assert flagged[0].remediation_actions is not None


def test_build_silver_passes_through_live_quote_cross_check(test_db_session):
    bars = _bars(300)
    provider = _FakeProvider(bars)
    run = pipe.ingest_bronze("AAPL", 400, provider, test_db_session)
    mismatched_quote = Quote(
        symbol="AAPL",
        price=bars[-1].close * 5,
        previous_close=bars[-1].close * 5,
        as_of=dt.datetime.now(dt.timezone.utc),
    )

    result = pipe.build_silver(
        "AAPL", test_db_session, ingestion_run=run, live_quote=mismatched_quote
    )

    assert result.report is not None
    assert result.report.passed is False
    assert any(i.type == "live_quote_mismatch" for i in result.report.issues)


def test_build_silver_with_no_bronze_run_is_a_noop(test_db_session):
    result = pipe.build_silver("NOPE", test_db_session)
    assert result.upserted == 0
    assert result.rejected == 0


# --- ensure_fresh --------------------------------------------------------


def test_ensure_fresh_ingests_when_no_silver_data(test_db_session):
    provider = _FakeProvider(_bars(300))

    pipe.ensure_fresh("AAPL", provider, test_db_session)

    assert provider.calls == 1
    assert test_db_session.query(models.SilverPriceBar).count() == 300


def test_ensure_fresh_noops_when_data_is_fresh(test_db_session):
    provider = _FakeProvider(_bars(300))
    pipe.ensure_fresh("AAPL", provider, test_db_session)
    assert provider.calls == 1

    pipe.ensure_fresh("AAPL", provider, test_db_session)

    assert provider.calls == 1


def test_ensure_fresh_reingests_when_stale(test_db_session):
    """Staleness is now defined by exchange-calendar coverage, not wall-
    clock age: removing the most recent trading session's Silver row
    (simulating "today's close hasn't landed yet, and now it has") makes
    FreshnessPolicy see a gap and re-ingest."""
    provider = _FakeProvider(_bars(300))
    pipe.ensure_fresh("AAPL", provider, test_db_session)
    assert provider.calls == 1

    # Delete the two most recent sessions - regardless of exactly when
    # "today" this test happens to run (even right at the market-close
    # boundary), yesterday's session has unambiguously already closed, so
    # this always leaves Silver at least one full completed session behind.
    ticker = test_db_session.query(models.Ticker).filter_by(symbol="AAPL").one()
    latest_rows = (
        test_db_session.query(models.SilverPriceBar)
        .filter_by(ticker_id=ticker.id)
        .order_by(models.SilverPriceBar.date.desc())
        .limit(2)
        .all()
    )
    for row in latest_rows:
        test_db_session.delete(row)
    test_db_session.commit()

    pipe.ensure_fresh("AAPL", provider, test_db_session)

    assert provider.calls == 2




# --- Gold products: read only Silver, persist lineage --------------------


def test_build_gold_technical_never_touches_provider(test_db_session):
    provider = _FakeProvider(_bars(300))
    pipe.ensure_fresh("AAPL", provider, test_db_session)
    assert provider.calls == 1

    def _boom(*args, **kwargs):
        raise AssertionError("build_gold_technical must not call the provider")

    provider.get_ohlcv = _boom
    provider.get_quote = _boom

    snapshot = pipe.build_gold_technical("AAPL", test_db_session)

    assert snapshot.lineage is not None
    assert snapshot.lineage.silver_record_count == 300
    assert snapshot.lineage.calculation_version == pipe.DEFAULT_CALCULATION_VERSION


def test_build_gold_technical_persists_row(test_db_session):
    provider = _FakeProvider(_bars(300))
    pipe.ensure_fresh("AAPL", provider, test_db_session)

    pipe.build_gold_technical("AAPL", test_db_session)

    record = test_db_session.query(models.TechnicalSnapshotRecord).one()
    assert record.silver_record_count == 300
    assert record.source_provider == "yahoo"
    assert record.bronze_ingestion_run_id is not None


def test_build_gold_technical_is_upsert_on_rerun(test_db_session):
    provider = _FakeProvider(_bars(300))
    pipe.ensure_fresh("AAPL", provider, test_db_session)

    pipe.build_gold_technical("AAPL", test_db_session)
    pipe.build_gold_technical("AAPL", test_db_session)

    assert test_db_session.query(models.TechnicalSnapshotRecord).count() == 1


def test_build_gold_market_structure(test_db_session):
    provider = _FakeProvider(_bars(300))
    pipe.ensure_fresh("AAPL", provider, test_db_session)

    snapshot = pipe.build_gold_market_structure("AAPL", test_db_session)

    assert snapshot.lineage.silver_record_count == 300
    assert test_db_session.query(models.MarketStructureSnapshotRecord).count() == 1


def test_build_gold_volume_liquidity(test_db_session):
    provider = _FakeProvider(_bars(300))
    pipe.ensure_fresh("AAPL", provider, test_db_session)

    snapshot = pipe.build_gold_volume_liquidity("AAPL", test_db_session)

    assert snapshot.lineage.silver_record_count == 300
    assert test_db_session.query(models.VolumeLiquiditySnapshotRecord).count() == 1


def test_build_gold_risk_with_benchmark(test_db_session):
    provider = _FakeProvider(_bars(300))
    pipe.ensure_fresh("AAPL", provider, test_db_session)
    pipe.ensure_fresh("SPY", provider, test_db_session)

    snapshot = pipe.build_gold_risk("AAPL", test_db_session, benchmark_symbol="SPY")

    assert snapshot.benchmark_symbol == "SPY"
    record = test_db_session.query(models.RiskSnapshotRecord).one()
    assert record.benchmark_symbol == "SPY"


def test_build_gold_risk_drops_missing_benchmark(test_db_session):
    provider = _FakeProvider(_bars(300))
    pipe.ensure_fresh("AAPL", provider, test_db_session)

    snapshot = pipe.build_gold_risk("AAPL", test_db_session, benchmark_symbol="NOSUCHBENCH")

    assert snapshot.benchmark_symbol is None


def test_build_gold_market_context_with_market_and_sector(test_db_session):
    provider = _FakeProvider(_bars(300))
    pipe.ensure_fresh("AAPL", provider, test_db_session)
    pipe.ensure_fresh("SPY", provider, test_db_session)
    pipe.ensure_fresh("XLK", provider, test_db_session)

    snapshot = pipe.build_gold_market_context(
        "AAPL", test_db_session, market_symbol="SPY", sector_symbol="XLK"
    )

    assert snapshot.market_symbol == "SPY"
    assert snapshot.sector_symbol == "XLK"
    record = test_db_session.query(models.MarketContextSnapshotRecord).one()
    assert record.market_symbol == "SPY"
    assert record.sector_symbol == "XLK"


# --- get_silver_bars boundary ---------------------------------------------


def test_get_silver_bars_returns_empty_for_unknown_symbol(test_db_session):
    assert pipe.get_silver_bars("NOPE", test_db_session) == []


def test_get_silver_bars_round_trips_ohlcv(test_db_session):
    bars = _bars(10)
    provider = _FakeProvider(bars)
    pipe.ensure_fresh("AAPL", provider, test_db_session)

    round_tripped = pipe.get_silver_bars("AAPL", test_db_session)

    assert len(round_tripped) == 10
    assert round_tripped[0].close == bars[0].close
    assert round_tripped[-1].date == bars[-1].date
