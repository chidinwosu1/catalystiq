import datetime as dt

from catalystiq.main import app
from catalystiq.providers.market_data import get_market_data_provider
from catalystiq.schemas.market_data import Quote


def _bar(date, close):
    from catalystiq.schemas.market_data import OHLCVBar

    return OHLCVBar(date=date, open=close, high=close + 0.5, low=close - 0.5, close=close, volume=1_000_000)


class _FakeProvider:
    def __init__(self, bars):
        self._bars = bars

    def get_ohlcv(self, symbol, start, end=None, interval="1d"):
        return self._bars

    def get_quote(self, symbol):
        return Quote(
            symbol=symbol.upper(),
            price=self._bars[-1].close,
            previous_close=self._bars[-1].close,
            as_of=dt.datetime.now(dt.timezone.utc),
        )

    def get_fundamentals(self, symbol):
        raise NotImplementedError

    def get_news(self, symbol, limit=10):
        raise NotImplementedError


def test_technical_snapshot_requires_auth():
    from fastapi.testclient import TestClient

    with TestClient(app) as unauth_client:
        r = unauth_client.get("/analysis/technical/AAPL")
        assert r.status_code in (401, 403)


def test_technical_snapshot_returns_computed_indicators(client):
    days = []
    d = dt.date(2020, 1, 2)
    while len(days) < 300:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    bars = [_bar(day, 100 + i * 0.5) for i, day in enumerate(days)]

    app.dependency_overrides[get_market_data_provider] = lambda: _FakeProvider(bars)
    try:
        r = client.get("/analysis/technical/UP")
        assert r.status_code == 200
        body = r.json()
        assert body["symbol"] == "UP"
        assert body["bars_used"] == len(bars)

        by_name = {i["name"]: i for i in body["indicators"]}
        assert by_name["rsi_14"]["status"] == "computed"
        assert by_name["rsi_14"]["value"] == 100.0
        assert by_name["rsi_14"]["percentile_5y"] is None  # under 3y of history
    finally:
        del app.dependency_overrides[get_market_data_provider]


def test_technical_snapshot_marks_insufficient_data_for_thin_history(client):
    days = []
    d = dt.date(2024, 1, 2)
    while len(days) < 5:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    bars = [_bar(day, 100) for day in days]

    app.dependency_overrides[get_market_data_provider] = lambda: _FakeProvider(bars)
    try:
        r = client.get("/analysis/technical/THIN")
        assert r.status_code == 200
        body = r.json()

        rsi = next(i for i in body["indicators"] if i["name"] == "rsi_14")
        assert rsi["status"] == "insufficient_data"
        assert rsi["value"] is None
    finally:
        del app.dependency_overrides[get_market_data_provider]


def test_technical_snapshot_502_on_market_data_error(client):
    from catalystiq.providers.market_data import MarketDataError

    class BrokenProvider:
        def get_ohlcv(self, symbol, start, end=None, interval="1d"):
            raise MarketDataError("upstream is down")

    app.dependency_overrides[get_market_data_provider] = lambda: BrokenProvider()
    try:
        r = client.get("/analysis/technical/BROKEN")
        assert r.status_code == 502
    finally:
        del app.dependency_overrides[get_market_data_provider]
