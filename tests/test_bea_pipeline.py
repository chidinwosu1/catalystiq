"""BEA vertical: adapter parsing (offline), Bronze->Silver, idempotency, and
error handling of BEA's in-body errors. No live BEA calls."""
import pytest

from catalystiq.db import models
from catalystiq.pipelines import bea_pipeline as bp
from catalystiq.providers.base import ProviderError, ProviderErrorCategory
from catalystiq.providers.bea import BeaProvider
from catalystiq.providers.transport import HttpResponse


class FakeTransport:
    def __init__(self, status, text):
        self._status, self._text = status, text
        self.requests = []

    def request(self, method, url, *, params=None, headers=None, json=None):
        self.requests.append({"url": url, "params": params})
        return HttpResponse(self._status, {}, self._text, url, 1.0, 0, "bea")


_BEA_OK = """
{"BEAAPI":{"Results":{"Data":[
 {"TableName":"T10105","LineNumber":"1","LineDescription":"Gross domestic product","SeriesCode":"A191RC","TimePeriod":"2024Q3","CL_UNIT":"Level","UNIT_MULT":"6","DataValue":"29,349.9"},
 {"TableName":"T10105","LineNumber":"2","LineDescription":"Personal consumption","SeriesCode":"DPCERC","TimePeriod":"2024Q3","CL_UNIT":"Level","UNIT_MULT":"6","DataValue":"(NA)"}
]}}}
"""

_BEA_ERR = '{"BEAAPI":{"Results":{"Error":{"APIErrorDescription":"Invalid table"}}}}'


def _provider(text=_BEA_OK, status=200):
    return BeaProvider("k", transport=FakeTransport(status, text))


def test_adapter_requires_key():
    with pytest.raises(ProviderError) as exc:
        BeaProvider("")
    assert exc.value.category is ProviderErrorCategory.CONFIG


def test_adapter_parses_and_handles_na():
    values = _provider().get_table("NIPA", "T10105", "Q")
    assert len(values) == 2
    gdp = [v for v in values if v.line_number == "1"][0]
    assert gdp.value == 29349.9
    assert gdp.scale == "6"
    na = [v for v in values if v.line_number == "2"][0]
    assert na.value is None  # "(NA)" -> None, never fabricated


def test_adapter_raises_on_bea_error_body():
    with pytest.raises(ProviderError) as exc:
        _provider(_BEA_ERR).get_table("NIPA", "BADTABLE", "Q")
    assert exc.value.category is ProviderErrorCategory.UNAVAILABLE


def test_ingest_build_silver_idempotent(test_db_session):
    db = test_db_session
    provider = _provider()
    run = bp.ingest_bea_table(provider, db, "NIPA", "T10105", "Q")
    assert run.status == "succeeded"
    assert bp.build_silver_bea(db, "NIPA", "T10105", "Q") == 2

    rows = bp.get_silver_bea(db, "NIPA", "T10105")
    assert len(rows) == 2
    assert rows[0].dataset == "NIPA"
    assert rows[0].bronze_ingestion_run_id == run.id

    # Reprocess -> no duplicates.
    bp.build_silver_bea(db, "NIPA", "T10105", "Q")
    assert db.query(models.SilverBeaValue).count() == 2
