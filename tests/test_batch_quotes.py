"""Batch quotes endpoint: per-symbol success/failure, no fabrication, dedupe."""
import datetime as dt

from catalystiq.main import app
from catalystiq.providers.market_data import MarketDataError, get_market_data_provider
from catalystiq.schemas.market_data import Quote


class _FakeProvider:
    """Returns a quote for known symbols; raises for 'BAD' to exercise the
    per-symbol unavailable path."""

    PRICES = {"AAPL": (195.0, 194.0), "^VIX": (16.8, 17.9)}

    def get_quote(self, symbol):
        symbol = symbol.upper()
        if symbol not in self.PRICES:
            raise MarketDataError(f"no quote for {symbol}")
        price, prev = self.PRICES[symbol]
        return Quote(
            symbol=symbol, price=price, previous_close=prev,
            as_of=dt.datetime(2026, 7, 18, tzinfo=dt.timezone.utc),
        )


def test_batch_quotes_mixed_success_and_failure(client):
    app.dependency_overrides[get_market_data_provider] = lambda: _FakeProvider()
    try:
        r = client.get("/market-data/quotes", params={"symbols": "AAPL, ^VIX ,BAD,aapl"})
    finally:
        del app.dependency_overrides[get_market_data_provider]
    assert r.status_code == 200
    rows = {row["symbol"]: row for row in r.json()}
    # Deduped (aapl == AAPL) -> three unique symbols.
    assert set(rows) == {"AAPL", "^VIX", "BAD"}

    aapl = rows["AAPL"]
    assert aapl["status"] == "ok"
    assert aapl["price"] == 195.0
    assert aapl["change"] == 1.0
    assert abs(aapl["change_pct"] - (1.0 / 194.0 * 100)) < 1e-9

    # A failing symbol is reported unavailable with no fabricated numbers.
    bad = rows["BAD"]
    assert bad["status"] == "unavailable"
    assert bad["price"] is None
    assert bad["change"] is None and bad["change_pct"] is None
