"""Sector-performance endpoint: deterministic 1D/1W/rel-strength from OHLCV,
per-ETF unavailable handling, no fabrication."""
import datetime as dt

from catalystiq.main import app
from catalystiq.providers.market_data import MarketDataError, get_market_data_provider
from catalystiq.schemas.market_data import OHLCVBar


def _bars(closes: list[float]) -> list[OHLCVBar]:
    base = dt.date(2026, 7, 1)
    return [
        OHLCVBar(date=base + dt.timedelta(days=i), open=c, high=c, low=c, close=c, volume=1000)
        for i, c in enumerate(closes)
    ]


class _FakeProvider:
    # 8 sessions; +1%/session -> positive daily & weekly. XLRE fails.
    def get_ohlcv(self, symbol, start, end=None, interval="1d"):
        symbol = symbol.upper()
        if symbol == "XLRE":
            raise MarketDataError("no data for XLRE")
        # SPY rises slightly slower so sectors show positive relative strength.
        step = 0.5 if symbol == "SPY" else 1.0
        return _bars([100.0 + i * step for i in range(8)])


def test_sectors_endpoint(client):
    app.dependency_overrides[get_market_data_provider] = lambda: _FakeProvider()
    try:
        r = client.get("/market-data/sectors")
    finally:
        del app.dependency_overrides[get_market_data_provider]
    assert r.status_code == 200
    rows = {row["sector"]: row for row in r.json()}
    assert len(rows) == 11  # all SPDR sectors present

    tech = rows["Technology"]
    assert tech["status"] == "ok"
    assert tech["daily_pct"] is not None and tech["weekly_pct"] is not None
    # Sector rose faster than SPY -> positive relative strength.
    assert tech["rel_strength_vs_spy"] > 0

    # The ETF that failed is reported unavailable with no fabricated numbers.
    re = rows["Real Estate"]
    assert re["status"] == "unavailable"
    assert re["daily_pct"] is None and re["weekly_pct"] is None
    assert re["rel_strength_vs_spy"] is None
