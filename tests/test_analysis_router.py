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


def test_market_structure_snapshot_requires_auth():
    from fastapi.testclient import TestClient

    with TestClient(app) as unauth_client:
        r = unauth_client.get("/analysis/AAPL/market-structure")
        assert r.status_code in (401, 403)


def test_market_structure_snapshot_returns_structure(client):
    import math

    days = []
    d = dt.date(2020, 1, 2)
    while len(days) < 300:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    bars = [_bar(day, 100 + i * 0.3 + 5 * math.sin(i / 10)) for i, day in enumerate(days)]

    app.dependency_overrides[get_market_data_provider] = lambda: _FakeProvider(bars)
    try:
        r = client.get("/analysis/UP/market-structure")
        assert r.status_code == 200
        body = r.json()
        assert body["symbol"] == "UP"
        assert body["trend_structure"]["value"] == "higher_highs_higher_lows"
        assert len(body["support_resistance_levels"]) > 0
    finally:
        del app.dependency_overrides[get_market_data_provider]


def test_market_structure_snapshot_502_on_market_data_error(client):
    from catalystiq.providers.market_data import MarketDataError

    class BrokenProvider:
        def get_ohlcv(self, symbol, start, end=None, interval="1d"):
            raise MarketDataError("upstream is down")

    app.dependency_overrides[get_market_data_provider] = lambda: BrokenProvider()
    try:
        r = client.get("/analysis/BROKEN/market-structure")
        assert r.status_code == 502
    finally:
        del app.dependency_overrides[get_market_data_provider]


def test_risk_snapshot_requires_auth():
    from fastapi.testclient import TestClient

    with TestClient(app) as unauth_client:
        r = unauth_client.get("/analysis/AAPL/risk")
        assert r.status_code in (401, 403)


def test_risk_snapshot_computes_with_benchmark(client):
    days = []
    d = dt.date(2019, 1, 2)
    while len(days) < 400:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    bars = [_bar(day, 100 + i * 0.1) for i, day in enumerate(days)]

    class TwoSymbolProvider:
        def get_ohlcv(self, symbol, start, end=None, interval="1d"):
            return bars

        def get_quote(self, symbol):
            return Quote(symbol=symbol.upper(), price=bars[-1].close, previous_close=bars[-1].close, as_of=dt.datetime.now(dt.timezone.utc))

        def get_fundamentals(self, symbol):
            raise NotImplementedError

        def get_news(self, symbol, limit=10):
            raise NotImplementedError

    app.dependency_overrides[get_market_data_provider] = lambda: TwoSymbolProvider()
    try:
        r = client.get("/analysis/UP/risk")
        assert r.status_code == 200
        body = r.json()
        assert body["symbol"] == "UP"
        assert body["benchmark_symbol"] == "SPY"
        by_name = {m["name"]: m for m in body["metrics"]}
        assert by_name["atr_14"]["status"] == "available"
        assert by_name["beta_vs_benchmark"]["status"] == "available"
    finally:
        del app.dependency_overrides[get_market_data_provider]


def test_risk_snapshot_502_on_primary_symbol_error(client):
    from catalystiq.providers.market_data import MarketDataError

    class BrokenProvider:
        def get_ohlcv(self, symbol, start, end=None, interval="1d"):
            raise MarketDataError("upstream is down")

    app.dependency_overrides[get_market_data_provider] = lambda: BrokenProvider()
    try:
        r = client.get("/analysis/BROKEN/risk")
        assert r.status_code == 502
    finally:
        del app.dependency_overrides[get_market_data_provider]


def test_risk_snapshot_degrades_gracefully_when_only_benchmark_fails(client):
    days = []
    d = dt.date(2019, 1, 2)
    while len(days) < 400:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    bars = [_bar(day, 100 + i * 0.1) for i, day in enumerate(days)]

    from catalystiq.providers.market_data import MarketDataError

    class BenchmarkFailsProvider:
        def get_ohlcv(self, symbol, start, end=None, interval="1d"):
            if symbol == "SPY":
                raise MarketDataError("benchmark unavailable")
            return bars

        def get_quote(self, symbol):
            return Quote(symbol=symbol.upper(), price=bars[-1].close, previous_close=bars[-1].close, as_of=dt.datetime.now(dt.timezone.utc))

        def get_fundamentals(self, symbol):
            raise NotImplementedError

        def get_news(self, symbol, limit=10):
            raise NotImplementedError

    app.dependency_overrides[get_market_data_provider] = lambda: BenchmarkFailsProvider()
    try:
        r = client.get("/analysis/UP/risk")
        assert r.status_code == 200
        body = r.json()
        by_name = {m["name"]: m for m in body["metrics"]}
        assert by_name["beta_vs_benchmark"]["status"] == "not_supported"
        assert any("Benchmark" in w for w in body["warnings"])
    finally:
        del app.dependency_overrides[get_market_data_provider]


def test_volume_liquidity_snapshot_requires_auth():
    from fastapi.testclient import TestClient

    with TestClient(app) as unauth_client:
        r = unauth_client.get("/analysis/AAPL/volume-liquidity")
        assert r.status_code in (401, 403)


def test_volume_liquidity_snapshot_returns_metrics(client):
    days = []
    d = dt.date(2020, 1, 2)
    while len(days) < 250:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    bars = [_bar(day, 100 + i * 0.1) for i, day in enumerate(days)]

    app.dependency_overrides[get_market_data_provider] = lambda: _FakeProvider(bars)
    try:
        r = client.get("/analysis/UP/volume-liquidity")
        assert r.status_code == 200
        body = r.json()
        assert body["symbol"] == "UP"
        by_name = {m["name"]: m for m in body["metrics"]}
        assert by_name["average_daily_volume_20d"]["status"] == "available"
        assert body["liquidity_classification"]["value"] in ("high", "moderate", "low", "very_low")
    finally:
        del app.dependency_overrides[get_market_data_provider]


def test_market_context_snapshot_requires_auth():
    from fastapi.testclient import TestClient

    with TestClient(app) as unauth_client:
        r = unauth_client.get("/analysis/AAPL/market-context")
        assert r.status_code in (401, 403)


def test_market_context_snapshot_resolves_sector_etf(client):
    days = []
    d = dt.date(2020, 1, 2)
    while len(days) < 300:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    bars = [_bar(day, 100 + i * 0.5) for i, day in enumerate(days)]

    class MultiSymbolProvider:
        def get_ohlcv(self, symbol, start, end=None, interval="1d"):
            return bars

        def get_quote(self, symbol):
            return Quote(symbol=symbol.upper(), price=bars[-1].close, previous_close=bars[-1].close, as_of=dt.datetime.now(dt.timezone.utc))

        def get_fundamentals(self, symbol):
            raise NotImplementedError

        def get_news(self, symbol, limit=10):
            raise NotImplementedError

    app.dependency_overrides[get_market_data_provider] = lambda: MultiSymbolProvider()
    try:
        r = client.get("/analysis/UP/market-context", params={"sector": "Technology"})
        assert r.status_code == 200
        body = r.json()
        assert body["market_symbol"] == "SPY"
        assert body["sector_symbol"] == "XLK"
        by_name = {m["name"]: m for m in body["metrics"]}
        assert by_name["relative_return_20d_vs_sector"]["status"] == "available"
    finally:
        del app.dependency_overrides[get_market_data_provider]


def test_market_context_snapshot_unmapped_sector_warns_and_omits(client):
    days = []
    d = dt.date(2020, 1, 2)
    while len(days) < 300:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    bars = [_bar(day, 100 + i * 0.5) for i, day in enumerate(days)]

    app.dependency_overrides[get_market_data_provider] = lambda: _FakeProvider(bars)
    try:
        r = client.get("/analysis/UP/market-context", params={"sector": "Not A Real Sector"})
        assert r.status_code == 200
        body = r.json()
        assert body["sector_symbol"] is None
        assert any("isn't mapped" in w for w in body["warnings"])
    finally:
        del app.dependency_overrides[get_market_data_provider]
