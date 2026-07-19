"""Ephemeral FRED client: adapter parsing offline via a fake transport.

No live FRED calls, no database. Persistence and compliance behaviors are
covered in tests/test_fred_compliance.py.
"""
import datetime as dt

import pytest

from catalystiq.fred.provider import FredClient
from catalystiq.providers.base import ProviderError, ProviderErrorCategory
from catalystiq.providers.transport import HttpResponse


class FakeTransport:
    """Returns a scripted HttpResponse per request; records params so tests can
    assert the ALFRED realtime params were sent."""

    def __init__(self, routes):
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


def _client(routes):
    return FredClient("dummy-key", transport=FakeTransport(routes))


def test_adapter_parses_series_and_missing_value():
    client = _client({"series/observations": (200, _OBS_JSON), "series": (200, _SERIES_JSON)})
    series = client.get_series("DGS10")
    assert series.series_id == "DGS10"
    assert series.frequency == "D"
    obs = client.get_observations("DGS10")
    assert obs[0].value == 4.25
    assert obs[1].value is None  # "." -> None, never fabricated


def test_adapter_missing_key_raises_config():
    with pytest.raises(ProviderError) as exc:
        FredClient("")
    assert exc.value.category is ProviderErrorCategory.CONFIG


def test_adapter_not_found_series_raises():
    client = _client({"series": (200, '{"seriess":[]}')})
    with pytest.raises(ProviderError) as exc:
        client.get_series("NOPE")
    assert exc.value.category is ProviderErrorCategory.NOT_FOUND


def test_as_of_sends_alfred_realtime_params():
    transport = FakeTransport({"series/observations": (200, _OBS_VINTAGE_JSON)})
    client = FredClient("dummy-key", transport=transport)
    client.get_observations("DGS10", as_of=dt.date(2026, 6, 20))
    obs_req = [r for r in transport.requests if "observations" in r["url"]][0]
    assert obs_req["params"]["realtime_start"] == "2026-06-20"
    assert obs_req["params"]["realtime_end"] == "2026-06-20"
    # The api_key is passed to the transport (which redacts it in logs).
    assert obs_req["params"]["api_key"] == "dummy-key"
