"""SEC EDGAR vertical: adapter parsing (offline via a fake transport),
Bronze->Silver normalization, amendment preservation (active-version
selection), material events, and the /fundamentals and /filings endpoints.
No live SEC calls."""
import datetime as dt

import pytest

from catalystiq.db import models
from catalystiq.pipelines import fundamentals_pipeline as fp
from catalystiq.providers.base import ProviderError, ProviderErrorCategory
from catalystiq.providers.fundamentals import SecEdgarProvider
from catalystiq.providers.transport import HttpResponse


class FakeTransport:
    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def request(self, method, url, *, params=None, headers=None, json=None):
        self.requests.append({"url": url, "headers": headers})
        for key, (status, text) in self.routes.items():
            if key in url:
                return HttpResponse(
                    status_code=status, headers={}, text=text, url=url,
                    elapsed_ms=1.0, retry_count=0, provider="sec_edgar",
                )
        return HttpResponse(404, {}, "{}", url, 1.0, 0, "sec_edgar")


_TICKERS = '{"0":{"cik_str":320193,"ticker":"AAPL","title":"Apple Inc."}}'

_SUBMISSIONS = """
{"cik":"320193","name":"Apple Inc.","tickers":["AAPL"],
 "filings":{"recent":{
   "accessionNumber":["0000320193-24-000123","0000320193-24-000100"],
   "form":["10-K","8-K"],
   "filingDate":["2024-11-01","2024-08-02"],
   "acceptanceDateTime":["2024-11-01T18:01:36.000Z","2024-08-02T16:30:00.000Z"],
   "reportDate":["2024-09-28","2024-08-01"],
   "primaryDocument":["aapl-20240928.htm","ea0201.htm"],
   "primaryDocDescription":["10-K","8-K"],
   "items":["","2.02,9.01"]
 }}}
"""

# Two vintages of the same concept/period: an original 10-K value and a later
# 10-K/A amendment that revises it. Both must be preserved.
_FACTS = """
{"cik":320193,"entityName":"Apple Inc.","facts":{"us-gaap":{"Revenues":{
  "label":"Revenues","units":{"USD":[
    {"start":"2023-10-01","end":"2024-09-28","val":383000000000,"accn":"0000320193-24-000123","fy":2024,"fp":"FY","form":"10-K","filed":"2024-11-01"},
    {"start":"2023-10-01","end":"2024-09-28","val":383285000000,"accn":"0000320193-25-000010","fy":2024,"fp":"FY","form":"10-K/A","filed":"2025-01-15"}
  ]}}}}}
"""


def _provider():
    routes = {
        "company_tickers.json": (200, _TICKERS),
        "submissions/CIK": (200, _SUBMISSIONS),
        "companyfacts/CIK": (200, _FACTS),
    }
    return SecEdgarProvider("Catalyst IQ test@example.com", transport=FakeTransport(routes))


def test_adapter_requires_user_agent():
    with pytest.raises(ProviderError) as exc:
        SecEdgarProvider("")
    assert exc.value.category is ProviderErrorCategory.CONFIG


def test_adapter_wires_user_agent_into_transport():
    # With the default (real) transport, the SEC-required User-Agent must be
    # set as a default header on every request.
    provider = SecEdgarProvider("Catalyst IQ test@example.com")
    assert provider._transport.default_headers["User-Agent"] == "Catalyst IQ test@example.com"


def test_resolve_cik_and_not_found():
    provider = _provider()
    ident = provider.resolve_cik("aapl")
    assert ident.cik == "0000320193"  # zero-padded to 10 digits
    assert ident.name == "Apple Inc."
    with pytest.raises(ProviderError) as exc:
        provider.resolve_cik("ZZZZ")
    assert exc.value.category is ProviderErrorCategory.NOT_FOUND


def test_adapter_parses_filings_and_material_events():
    provider = _provider()
    filings = provider.get_filings("0000320193")
    forms = {f.form for f in filings}
    assert forms == {"10-K", "8-K"}
    events = provider.get_material_events("0000320193")
    assert len(events) == 1
    assert events[0].form == "8-K"
    assert events[0].items == ["2.02", "9.01"]


def test_adapter_parses_xbrl_facts_with_amendment_flag():
    provider = _provider()
    facts = provider.get_company_facts("0000320193")
    assert len(facts) == 2
    amended = [f for f in facts if f.is_amendment]
    assert len(amended) == 1
    assert amended[0].form == "10-K/A"


def test_ingest_and_build_silver(test_db_session):
    db = test_db_session
    provider = _provider()
    run = fp.ingest_company(provider, db, "AAPL")
    assert run.status == "succeeded"
    assert run.domain == "fundamentals"
    assert run.requested_identifier == "0000320193"
    # 4 raw documents stored.
    assert db.query(models.BronzeRawDocument).count() == 4

    result = fp.build_silver_all(db, "0000320193")
    assert result["identifier"] is True
    assert result["filings"] == 2
    assert result["material_events"] == 1
    assert result["facts"] == 2


def test_amendment_preserved_and_active_version_selected(test_db_session):
    db = test_db_session
    provider = _provider()
    fp.ingest_company(provider, db, "AAPL")
    fp.build_silver_all(db, "0000320193")

    # Both vintages of Revenues FY2024 coexist.
    revenue_rows = (
        db.query(models.SilverCompanyFact)
        .filter_by(cik="0000320193", concept="Revenues")
        .all()
    )
    assert len(revenue_rows) == 2
    values = sorted(r.value for r in revenue_rows)
    assert values == [383000000000.0, 383285000000.0]

    # Active version = latest filed (the 10-K/A amendment).
    active = fp.get_active_facts(db, "0000320193")
    active_revenue = [f for f in active if f.concept == "Revenues"]
    assert len(active_revenue) == 1
    assert active_revenue[0].value == 383285000000.0
    assert active_revenue[0].is_amendment is True


def test_build_silver_idempotent(test_db_session):
    db = test_db_session
    provider = _provider()
    fp.ingest_company(provider, db, "AAPL")
    fp.build_silver_all(db, "0000320193")
    fp.ingest_company(provider, db, "AAPL")
    fp.build_silver_all(db, "0000320193")
    # No duplicate filings/facts on reprocessing.
    assert db.query(models.SilverCompanyFiling).count() == 2
    assert db.query(models.SilverCompanyFact).count() == 2


def test_fundamentals_endpoint_serves_silver(client, test_db_session):
    db = test_db_session
    provider = _provider()
    fp.ingest_company(provider, db, "AAPL")
    fp.build_silver_all(db, "0000320193")

    resp = client.get("/fundamentals/AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["security"]["cik"] == "0000320193"
    # Only the active (amended) revenue fact is returned.
    revenues = [f for f in body["active_facts"] if f["concept"] == "Revenues"]
    assert len(revenues) == 1
    assert revenues[0]["value"] == 383285000000.0

    filings = client.get("/filings/AAPL")
    assert filings.status_code == 200
    assert {f["form"] for f in filings.json()} == {"10-K", "8-K"}


def test_fundamentals_endpoint_404_when_unavailable(client):
    # SEC disabled by default and no Silver -> 404, not a crash.
    resp = client.get("/fundamentals/AAPL")
    assert resp.status_code == 404
