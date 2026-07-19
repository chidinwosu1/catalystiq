"""FRED/ALFRED vertical: adapter parsing (offline via a fake transport),
Bronze->Silver normalization, point-in-time vintage preservation, and the
/macro endpoints. No live FRED calls."""
import datetime as dt

import pytest

from catalystiq.db import models
from catalystiq.pipelines import macro_pipeline as mp
from catalystiq.providers.base import ProviderError, ProviderErrorCategory
from catalystiq.providers.macro import FredProvider
from catalystiq.providers.transport import HttpResponse


class FakeTransport:
    """Returns a scripted HttpResponse per request; records params so tests
    can assert the ALFRED realtime params were sent."""

    def __init__(self, routes):
        # routes: dict mapping url-substring -> (status, json_text)
        self.routes = routes
        self.requests: list[dict] = []

    def request(self, method, url, *, params=None, headers=None, json=None):
        self.requests.append({"url": url, "params": params})
        for key, (status, text) in self.routes.items():
            if key in url:
                return HttpResponse(
                    status_code=status, headers={}, text=text, url=url,
                    elapsed_ms=1.0, retry_count=0, provider="fred",
                )
        return HttpResponse(
            status_code=404, headers={}, text="{}", url=url, elapsed_ms=1.0,
            retry_count=0, provider="fred",
        )


_SERIES_JSON = """
{"seriess":[{"id":"DGS10","title":"10-Year Treasury","frequency_short":"D",
"units_short":"%","seasonal_adjustment_short":"NSA",
"observation_start":"1962-01-02","observation_end":"2026-07-17"}]}
"""

_OBS_JSON = """
{"units":"%","observations":[
{"realtime_start":"2026-07-01","realtime_end":"2026-07-31","date":"2026-06-30","value":"4.25"},
{"realtime_start":"2026-07-01","realtime_end":"2026-07-31","date":"2026-07-01","value":"."}
]}
"""

_OBS_VINTAGE_JSON = """
{"units":"%","observations":[
{"realtime_start":"2026-06-15","realtime_end":"2026-06-30","date":"2026-06-30","value":"4.10"}
]}
"""


def _provider(routes):
    return FredProvider("dummy-key", transport=FakeTransport(routes))


def test_adapter_parses_series_and_missing_value():
    provider = _provider({"series/observations": (200, _OBS_JSON), "series": (200, _SERIES_JSON)})
    series = provider.get_series("DGS10")
    assert series.series_id == "DGS10"
    assert series.frequency == "D"
    obs = provider.get_observations("DGS10")
    assert obs[0].value == 4.25
    assert obs[1].value is None  # "." -> None, never fabricated


def test_adapter_missing_key_raises_config():
    with pytest.raises(ProviderError) as exc:
        FredProvider("")
    assert exc.value.category is ProviderErrorCategory.CONFIG


def test_adapter_not_found_series_raises():
    provider = _provider({"series": (200, '{"seriess":[]}')})
    with pytest.raises(ProviderError) as exc:
        provider.get_series("NOPE")
    assert exc.value.category is ProviderErrorCategory.NOT_FOUND


def test_as_of_sends_alfred_realtime_params():
    transport = FakeTransport({"series/observations": (200, _OBS_VINTAGE_JSON)})
    provider = FredProvider("dummy-key", transport=transport)
    provider.get_observations("DGS10", as_of=dt.date(2026, 6, 20))
    obs_req = [r for r in transport.requests if "observations" in r["url"]][0]
    assert obs_req["params"]["realtime_start"] == "2026-06-20"
    assert obs_req["params"]["realtime_end"] == "2026-06-20"
    # The api_key is passed to the transport (which redacts it in logs).
    assert obs_req["params"]["api_key"] == "dummy-key"


def test_ingest_build_silver_and_missing_value_warning(test_db_session):
    db = test_db_session
    provider = _provider({"series/observations": (200, _OBS_JSON), "series": (200, _SERIES_JSON)})
    run = mp.ingest_series(provider, db, "DGS10")
    assert run.status == "succeeded"
    assert run.record_count == 2
    assert run.domain == "macro"

    assert mp.build_silver_series(db, "DGS10") is True
    assert mp.build_silver_observations(db, "DGS10") == 2

    series = mp.get_silver_series(db, "DGS10")
    assert series.units == "%"
    obs = mp.get_silver_observations(db, "DGS10")
    assert [o.observation_date for o in obs] == [dt.date(2026, 6, 30), dt.date(2026, 7, 1)]
    missing = [o for o in obs if o.value is None][0]
    assert missing.validation_status == "clean_with_warnings"


def test_vintages_coexist_never_overwrite(test_db_session):
    db = test_db_session
    # First ingest: latest vintage (realtime_start 2026-07-01) for 2026-06-30.
    p1 = _provider({"series/observations": (200, _OBS_JSON), "series": (200, _SERIES_JSON)})
    mp.ingest_series(p1, db, "DGS10")
    mp.build_silver_observations(db, "DGS10")

    # Second ingest: an EARLIER vintage (realtime_start 2026-06-15) of the
    # same observation date with a different (pre-revision) value.
    p2 = _provider({"series/observations": (200, _OBS_VINTAGE_JSON), "series": (200, _SERIES_JSON)})
    mp.ingest_series(p2, db, "DGS10", as_of=dt.date(2026, 6, 20))
    mp.build_silver_observations(db, "DGS10")

    same_date = (
        db.query(models.SilverMacroObservation)
        .filter_by(series_id="DGS10", observation_date=dt.date(2026, 6, 30))
        .all()
    )
    # Both vintages of 2026-06-30 must coexist - the original 4.10 is not
    # overwritten by the revised 4.25.
    values = sorted(o.value for o in same_date)
    assert values == [4.10, 4.25]

    # Point-in-time read as of 2026-06-20 returns the 4.10 vintage.
    as_of = mp.get_silver_observations(db, "DGS10", as_of=dt.date(2026, 6, 20))
    by_date = {o.observation_date: o for o in as_of}
    assert by_date[dt.date(2026, 6, 30)].value == 4.10


def test_ingest_idempotent_same_vintage(test_db_session):
    db = test_db_session
    provider = _provider({"series/observations": (200, _OBS_JSON), "series": (200, _SERIES_JSON)})
    mp.ingest_series(provider, db, "DGS10")
    mp.build_silver_observations(db, "DGS10")
    mp.ingest_series(provider, db, "DGS10")
    mp.build_silver_observations(db, "DGS10")
    # Same vintage reprocessed -> no duplicate rows.
    assert db.query(models.SilverMacroObservation).count() == 2


def test_macro_endpoint_serves_silver_when_provider_disabled(client, test_db_session):
    # FRED disabled by default -> endpoint serves existing Silver, no error.
    db = test_db_session
    provider = _provider({"series/observations": (200, _OBS_JSON), "series": (200, _SERIES_JSON)})
    mp.ingest_series(provider, db, "DGS10")
    mp.build_silver_series(db, "DGS10")

    resp = client.get("/macro/series/DGS10")
    assert resp.status_code == 200
    assert resp.json()["series_id"] == "DGS10"
