"""Nasdaq Trader vertical: pipe-file parsing (offline), stable internal
security ids, Bronze->Silver security master, idempotency. No live calls."""
from catalystiq.db import models
from catalystiq.pipelines import regulatory_pipeline as rp
from catalystiq.providers.nasdaq_trader import NasdaqTraderProvider
from catalystiq.providers.transport import HttpResponse


class FakeTransport:
    def __init__(self, mapping):
        self.mapping = mapping  # url-substring -> text

    def request(self, method, url, *, params=None, headers=None, json=None):
        for key, text in self.mapping.items():
            if key in url:
                return HttpResponse(200, {}, text, url, 1.0, 0, "nasdaq_trader")
        return HttpResponse(404, {}, "", url, 1.0, 0, "nasdaq_trader")


_NASDAQ = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N
TEST|Test Issue|Q|Y|N|100|N|N
File Creation Time: 0718202608:00|||||||"""

_OTHER = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
SPY|SPDR S&P 500 ETF Trust|P|SPY|Y|100|N|SPY
File Creation Time: 0718202608:00||||||||"""


def _provider():
    return NasdaqTraderProvider(
        transport=FakeTransport({"nasdaqlisted": _NASDAQ, "otherlisted": _OTHER})
    )


def test_parse_nasdaq_listed_stable_internal_id():
    entries = _provider().parse_nasdaq_listed(_NASDAQ)
    by_symbol = {e.symbol: e for e in entries}
    assert "AAPL" in by_symbol
    # Internal id is not the bare ticker.
    assert by_symbol["AAPL"].internal_security_id == "NASDAQ:AAPL"
    assert by_symbol["AAPL"].internal_security_id != "AAPL"
    # Test issue flagged inactive.
    assert by_symbol["TEST"].test_issue is True
    assert by_symbol["TEST"].is_active is False
    # Trailer line skipped.
    assert "File Creation Time" not in by_symbol


def test_parse_other_listed_etf_flag():
    entries = _provider().parse_other_listed(_OTHER)
    spy = [e for e in entries if e.symbol == "SPY"][0]
    assert spy.etf is True
    assert spy.exchange == "P"
    assert spy.internal_security_id == "P:SPY"


def test_get_securities_combines_both_files():
    entries = _provider().get_securities()
    assert {e.symbol for e in entries} == {"AAPL", "TEST", "SPY"}


def test_ingest_build_silver_idempotent(test_db_session):
    db = test_db_session
    provider = _provider()
    run = rp.ingest_security_master(provider, db)
    assert run.status == "succeeded"
    assert run.record_count == 3

    assert rp.build_silver_security_master(db) == 3
    aapl = rp.get_security_master(db, "AAPL")
    assert len(aapl) == 1
    assert aapl[0].internal_security_id == "NASDAQ:AAPL"

    # Reprocess -> no duplicates.
    rp.build_silver_security_master(db)
    assert db.query(models.SilverSecurityMaster).count() == 3
