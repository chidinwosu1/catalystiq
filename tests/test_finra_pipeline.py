"""FINRA vertical: header-driven parsing of reg-SHO short-sale-volume and
short-interest files (offline), separate Silver datasets, corrected-file
preservation, and the endpoints. No live FINRA calls."""
import datetime as dt

from catalystiq.db import models
from catalystiq.pipelines import regulatory_pipeline as rp
from catalystiq.providers.finra import FinraProvider
from catalystiq.providers.transport import HttpResponse


class FakeTransport:
    def __init__(self, text, status=200):
        self._text, self._status = text, status
        self.requests = []

    def request(self, method, url, *, params=None, headers=None, json=None):
        self.requests.append(url)
        return HttpResponse(self._status, {}, self._text, url, 1.0, 0, "finra")


_SHVOL = """Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market
20260706|AAPL|12345|100|45678|CNMS
20260706|MSFT|5000|0|20000|CNMS
Sample line: total records 2"""

_SHVOL_CORRECTED = """Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market
20260706|AAPL|22222|100|45678|CNMS"""

_SHORT_INTEREST = """symbolCode|settlementDate|publicationDate|currentShortPositionQuantity|previousShortPositionQuantity|daysToCoverQuantity
AAPL|20260630|20260709|108000000|110000000|1.2
MSFT|20260630|20260709|40000000|41000000|0.9"""


def test_parse_short_sale_volume_header_driven():
    provider = FinraProvider(transport=FakeTransport(_SHVOL))
    rows = provider.get_short_sale_volume(dt.date(2026, 7, 6))
    symbols = {r.symbol for r in rows}
    assert symbols == {"AAPL", "MSFT"}  # numeric junk line skipped
    aapl = [r for r in rows if r.symbol == "AAPL"][0]
    assert aapl.short_volume == 12345
    assert aapl.total_volume == 45678
    assert aapl.reporting_facility == "CNMS"


def test_short_sale_volume_bronze_silver(test_db_session):
    db = test_db_session
    provider = FinraProvider(transport=FakeTransport(_SHVOL))
    run = rp.ingest_short_sale_volume(provider, db, dt.date(2026, 7, 6))
    assert run.status == "succeeded"
    assert run.domain == "regulatory"
    assert rp.build_silver_short_sale_volume(db, dt.date(2026, 7, 6)) == 2

    rows = rp.get_short_sale_volume(db, "AAPL")
    assert len(rows) == 1
    assert rows[0].short_volume == 12345


def test_corrected_file_preserved_alongside_original(test_db_session):
    db = test_db_session
    orig = FinraProvider(transport=FakeTransport(_SHVOL))
    rp.ingest_short_sale_volume(orig, db, dt.date(2026, 7, 6), file_version="original")
    rp.build_silver_short_sale_volume(db, dt.date(2026, 7, 6), file_version="original")

    corrected = FinraProvider(transport=FakeTransport(_SHVOL_CORRECTED))
    rp.ingest_short_sale_volume(corrected, db, dt.date(2026, 7, 6), file_version="corrected")
    rp.build_silver_short_sale_volume(db, dt.date(2026, 7, 6), file_version="corrected")

    aapl = (
        db.query(models.SilverShortSaleVolume)
        .filter_by(symbol="AAPL", trade_date=dt.date(2026, 7, 6))
        .all()
    )
    # Both the original (12345) and corrected (22222) are preserved.
    assert sorted(r.short_volume for r in aapl) == [12345, 22222]
    assert {r.file_version for r in aapl} == {"original", "corrected"}


def test_short_interest_separate_dataset(test_db_session):
    db = test_db_session
    provider = FinraProvider(transport=FakeTransport(_SHORT_INTEREST))
    run = rp.ingest_short_interest_text(provider, db, _SHORT_INTEREST, settlement_hint="20260630")
    assert run.status == "succeeded"
    assert rp.build_silver_short_interest_from_run(db, run.id) == 2

    si = rp.get_short_interest(db, "AAPL")
    assert len(si) == 1
    assert si[0].settlement_date == dt.date(2026, 6, 30)
    assert si[0].short_interest_quantity == 108000000
    assert si[0].publication_date == dt.date(2026, 7, 9)
    # Short interest and short-sale volume are separate tables.
    assert db.query(models.SilverShortSaleVolume).count() == 0


def test_endpoints_serve_silver(client, test_db_session):
    db = test_db_session
    provider = FinraProvider(transport=FakeTransport(_SHORT_INTEREST))
    run = provider_run = rp.ingest_short_interest_text(provider, db, _SHORT_INTEREST, settlement_hint="20260630")
    rp.build_silver_short_interest_from_run(db, run.id)

    resp = client.get("/short-interest/AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["short_interest_quantity"] == 108000000

    empty = client.get("/short-sale-volume/AAPL")
    assert empty.status_code == 200
    assert empty.json() == []
