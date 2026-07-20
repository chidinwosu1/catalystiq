"""Point-in-time SEC fundamentals: vintage + amendment + look-ahead."""
import datetime as dt

from catalystiq.db import models
from catalystiq.ml.features.fundamentals_pit import pit_fundamental_features
from catalystiq.ml.features.schema import DataQualityStatus


def _ident(db, symbol="AAPL", cik="0000320193"):
    db.add(models.SilverSecurityIdentifier(
        stable_identifier=cik, provider="sec_edgar", cik=cik, symbol=symbol,
        retrieved_at=dt.datetime(2021, 1, 1), created_at=dt.datetime(2021, 1, 1),
    ))


def _fact(db, *, concept, value, ps, pe, filing_date, accession, is_amendment=False,
          fiscal_year=None, fiscal_period="FY", cik="0000320193", unit="USD"):
    db.add(models.SilverCompanyFact(
        stable_identifier=cik, provider="sec_edgar", cik=cik, taxonomy="us-gaap",
        concept=concept, unit=unit, value=value, period_start=ps, period_end=pe,
        form="10-K/A" if is_amendment else "10-K", filing_date=filing_date,
        accession_number=accession, is_amendment=is_amendment,
        fiscal_year=fiscal_year, fiscal_period=fiscal_period,
        retrieved_at=dt.datetime(2021, 1, 1), created_at=dt.datetime(2021, 1, 1),
    ))


def _filing(db, *, filing_date, accession, form="10-Q", cik="0000320193"):
    db.add(models.SilverCompanyFiling(
        stable_identifier=cik, provider="sec_edgar", cik=cik, form=form,
        accession_number=accession, filing_date=filing_date,
        retrieved_at=dt.datetime(2021, 1, 1), created_at=dt.datetime(2021, 1, 1),
    ))


def _seed_revenue(db):
    _ident(db)
    _fact(db, concept="Revenues", value=1000.0, ps=dt.date(2019, 1, 1), pe=dt.date(2019, 12, 31),
          filing_date=dt.date(2020, 2, 15), accession="a-2019", fiscal_year=2019)
    _fact(db, concept="Revenues", value=1200.0, ps=dt.date(2020, 1, 1), pe=dt.date(2020, 12, 31),
          filing_date=dt.date(2021, 2, 15), accession="a-2020", fiscal_year=2020)
    db.flush()


def _yoy(db, symbol, ts):
    feats = {f.feature_name: f for f in pit_fundamental_features(
        db, symbol, ts, as_of=ts.date(), retrieved_at=ts)}
    return feats


def test_revenue_yoy_point_in_time(test_db_session):
    _seed_revenue(test_db_session)
    f = _yoy(test_db_session, "AAPL", dt.datetime(2021, 6, 1, 20))["pit_revenue_yoy"]
    assert f.data_quality_status is DataQualityStatus.OK
    assert abs(f.feature_value - 0.20) < 1e-9
    # available_at is the governing (latest) filing date, not "now"
    assert f.available_at_timestamp.date() == dt.date(2021, 2, 15)


def test_no_lookahead_future_period_not_used(test_db_session):
    _seed_revenue(test_db_session)
    # As of a date BEFORE the FY2020 filing, only FY2019 exists -> can't form YoY.
    f = _yoy(test_db_session, "AAPL", dt.datetime(2020, 6, 1, 20))["pit_revenue_yoy"]
    assert f.data_quality_status is DataQualityStatus.MISSING


def test_amendment_supersedes_only_when_public(test_db_session):
    _seed_revenue(test_db_session)
    # Restatement of FY2020 revenue to 1300, filed 2021-08-01.
    _fact(test_db_session, concept="Revenues", value=1300.0, ps=dt.date(2020, 1, 1),
          pe=dt.date(2020, 12, 31), filing_date=dt.date(2021, 8, 1), accession="a-2020-amd",
          is_amendment=True, fiscal_year=2020)
    test_db_session.flush()

    # Before the amendment is public: original 1200 -> YoY 0.20 (revision ignored)
    before = _yoy(test_db_session, "AAPL", dt.datetime(2021, 6, 1, 20))["pit_revenue_yoy"]
    assert abs(before.feature_value - 0.20) < 1e-9
    # After the amendment is public: 1300 -> YoY 0.30 (amendment supersedes)
    after = _yoy(test_db_session, "AAPL", dt.datetime(2021, 9, 1, 20))["pit_revenue_yoy"]
    assert abs(after.feature_value - 0.30) < 1e-9


def test_gross_margin_point_in_time(test_db_session):
    _ident(test_db_session)
    _fact(test_db_session, concept="Revenues", value=1200.0, ps=dt.date(2020, 1, 1),
          pe=dt.date(2020, 12, 31), filing_date=dt.date(2021, 2, 15), accession="r", fiscal_year=2020)
    _fact(test_db_session, concept="CostOfRevenue", value=800.0, ps=dt.date(2020, 1, 1),
          pe=dt.date(2020, 12, 31), filing_date=dt.date(2021, 2, 15), accession="c", fiscal_year=2020)
    test_db_session.flush()
    f = _yoy(test_db_session, "AAPL", dt.datetime(2021, 6, 1, 20))["pit_gross_margin"]
    assert f.data_quality_status is DataQualityStatus.OK
    assert abs(f.feature_value - (400.0 / 1200.0)) < 1e-9


def test_recent_filing_event_flag(test_db_session):
    _ident(test_db_session)
    _filing(test_db_session, filing_date=dt.date(2021, 5, 25), accession="f1")
    test_db_session.flush()
    # Within 14 days after the filing -> 1 (a real, computed 0/1, never MISSING)
    hit = _yoy(test_db_session, "AAPL", dt.datetime(2021, 6, 1, 20))["recent_filing_event"]
    assert hit.feature_value == 1.0
    # Well after the window -> 0
    miss = _yoy(test_db_session, "AAPL", dt.datetime(2021, 7, 1, 20))["recent_filing_event"]
    assert miss.feature_value == 0.0


def test_fail_closed_when_no_cik(test_db_session):
    # No identifier row for the symbol -> all fundamentals MISSING (fail closed).
    feats = _yoy(test_db_session, "ZZZZ", dt.datetime(2021, 6, 1, 20))
    for name in ("pit_revenue_yoy", "pit_gross_margin", "recent_filing_event"):
        assert feats[name].data_quality_status is DataQualityStatus.MISSING
