"""BLS vertical: adapter parsing (offline via fake transport), normalization
into the shared macro-observation Silver model with BLS source_fields
preserved, and the /macro observations endpoint with source=bls. No live
BLS calls."""
import datetime as dt

import pytest

from catalystiq.db import models
from catalystiq.pipelines import macro_pipeline as mp
from catalystiq.providers.base import ProviderError, ProviderErrorCategory
from catalystiq.providers.bls import BlsProvider, _period_to_date
from catalystiq.providers.transport import HttpResponse


class FakeTransport:
    def __init__(self, status, text):
        self._status = status
        self._text = text
        self.requests = []

    def request(self, method, url, *, params=None, headers=None, json=None):
        self.requests.append({"method": method, "url": url, "json": json})
        return HttpResponse(self._status, {}, self._text, url, 1.0, 0, "bls")


_BLS_OK = """
{"status":"REQUEST_SUCCEEDED","Results":{"series":[
 {"seriesID":"LNS14000000","data":[
   {"year":"2026","period":"M06","periodName":"June","value":"4.1","footnotes":[{}]},
   {"year":"2026","period":"M05","periodName":"May","value":"4.0","footnotes":[{"code":"P","text":"preliminary"}]}
 ]}
]}}
"""

_BLS_FAIL = '{"status":"REQUEST_NOT_PROCESSED","message":["invalid key"]}'


def test_period_to_date_mapping():
    assert _period_to_date(2026, "M06") == dt.date(2026, 6, 1)
    assert _period_to_date(2026, "M13") == dt.date(2026, 1, 1)  # annual avg
    assert _period_to_date(2026, "Q02") == dt.date(2026, 4, 1)
    assert _period_to_date(2026, "A01") == dt.date(2026, 1, 1)
    assert _period_to_date(2026, "M99") is None


def test_adapter_requires_key():
    with pytest.raises(ProviderError) as exc:
        BlsProvider("")
    assert exc.value.category is ProviderErrorCategory.CONFIG


def test_adapter_parses_observations_and_preliminary_flag():
    provider = BlsProvider("k", transport=FakeTransport(200, _BLS_OK))
    obs = provider.get_observations("LNS14000000")
    assert {o.observation_date for o in obs} == {dt.date(2026, 6, 1), dt.date(2026, 5, 1)}
    may = [o for o in obs if o.observation_date == dt.date(2026, 5, 1)][0]
    assert may.value == 4.0
    assert may.source == "bls"
    assert may.source_fields["preliminary"] is True
    assert may.source_fields["period"] == "M05"


def test_adapter_raises_on_bls_failure():
    provider = BlsProvider("k", transport=FakeTransport(200, _BLS_FAIL))
    with pytest.raises(ProviderError) as exc:
        provider.get_observations("LNS14000000")
    assert exc.value.category is ProviderErrorCategory.UNAVAILABLE


def test_ingest_and_build_silver_shared_model(test_db_session):
    db = test_db_session
    provider = BlsProvider("k", transport=FakeTransport(200, _BLS_OK))
    run = mp.ingest_bls_series(provider, db, "LNS14000000")
    assert run.status == "succeeded"
    assert run.provider == "bls"
    assert run.domain == "macro"

    n = mp.build_silver_observations(db, "LNS14000000", provider="bls")
    assert n == 2
    rows = mp.get_silver_observations(db, "LNS14000000", provider="bls")
    assert len(rows) == 2
    # BLS-specific fields preserved in the shared macro-observation model.
    may = [r for r in rows if r.observation_date == dt.date(2026, 5, 1)][0]
    assert may.provider == "bls"
    assert may.source_fields["preliminary"] is True
    assert may.validation_status == "clean"


def test_bls_silver_rows_are_provider_scoped(test_db_session):
    # Provider is part of the Silver identity, so a series id is namespaced by
    # provider and cannot collide with another source's rows.
    db = test_db_session
    bls = BlsProvider("k", transport=FakeTransport(200, _BLS_OK.replace("LNS14000000", "SHARED")))
    mp.ingest_bls_series(bls, db, "SHARED")
    mp.build_silver_observations(db, "SHARED", provider="bls")
    assert db.query(models.SilverMacroObservation).filter_by(provider="bls").count() == 2
