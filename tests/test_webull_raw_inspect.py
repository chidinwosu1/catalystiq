"""The /paper/webull-raw inspection endpoint dumps each raw Webull call in
isolation, so one failing probe doesn't hide the others."""
from __future__ import annotations

from catalystiq.main import app
from catalystiq.providers.broker import get_broker_provider


class _RawBroker:
    def get_account_balance_raw(self):
        return {"accountId": "DEM1", "totalMarketValue": "1000.00", "cashBalance": "500.00"}

    def get_positions_raw(self):
        return {"positions": [{"symbol": "AAPL", "quantity": "10"}]}

    def get_orders(self):
        raise RuntimeError("orders shape unconfirmed")


def test_webull_raw_returns_each_probe_isolated(client):
    app.dependency_overrides[get_broker_provider] = lambda: _RawBroker()
    try:
        r = client.get("/paper/webull-raw")
    finally:
        del app.dependency_overrides[get_broker_provider]

    assert r.status_code == 200
    body = r.json()
    # Successful probes return their raw JSON...
    assert body["account_balance"]["cashBalance"] == "500.00"
    assert body["positions"]["positions"][0]["symbol"] == "AAPL"
    # ...and a failing one is reported inline without breaking the others.
    assert "error" in body["orders"]
    assert "RuntimeError" in body["orders"]["error"]
