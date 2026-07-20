"""Read-only /paper endpoints: accounts, order-history, reconcile. The broker
dependency is overridden with a fake so no SDK/network is involved."""
import pytest

from catalystiq.main import app
from catalystiq.providers.broker import get_broker_provider
from catalystiq.schemas.broker import AccountInfo, BrokerAccount, OrderRecord, Position


class FakeBroker:
    _account_id = "APIID-ABC123"

    def get_account_list(self):
        return [
            BrokerAccount(
                account_id="APIID-ABC123",
                account_number="DEM34946",
                account_type="MARGIN",
                currency="USD",
                status="ACTIVE",
            )
        ]

    def get_order_history(
        self, start_date=None, end_date=None, symbol=None, filled_only=False
    ):
        orders = [
            OrderRecord(
                order_id="ORD-1",
                client_order_id="COID-1",
                symbol="VOO",
                side="BUY",
                order_type="MARKET",
                status="filled",
                status_raw="FILLED",
                total_qty="1",
                filled_qty="1",
                avg_fill_price="684.65",
                filled_amount="684.65",
            )
        ]
        if symbol:
            orders = [o for o in orders if o.symbol.upper() == symbol.upper()]
        if filled_only:
            orders = [o for o in orders if o.is_filled]
        return orders

    def get_positions(self):
        return [
            Position(
                symbol="VOO",
                side="long",
                qty="1",
                avg_entry_price="684.65",
                market_value="682.91",
                cost_basis="684.65",
                unrealized_pl="-1.74",
                unrealized_plpc="-0.0025",
                current_price="682.91",
                change_today="-1.74",
            )
        ]

    def get_account(self):
        return AccountInfo(
            status="ACTIVE",
            currency="USD",
            cash="999315.35",
            buying_power="3999310.16",
            portfolio_value="999998.27",
            equity="999998.27",
            last_equity="1000000.00",
            trading_blocked=False,
            account_blocked=False,
            pattern_day_trader=False,
        )


@pytest.fixture
def broker_client(client):
    app.dependency_overrides[get_broker_provider] = lambda: FakeBroker()
    yield client
    app.dependency_overrides.pop(get_broker_provider, None)


def test_accounts_endpoint(broker_client):
    resp = broker_client.get("/paper/accounts")
    assert resp.status_code == 200
    [acct] = resp.json()
    assert acct["account_number"] == "DEM34946"
    assert acct["account_id"] == "APIID-ABC123"


def test_order_history_filled_filter(broker_client):
    resp = broker_client.get("/paper/order-history", params={"filled_only": True})
    assert resp.status_code == 200
    orders = resp.json()
    assert len(orders) == 1
    assert orders[0]["symbol"] == "VOO"
    assert orders[0]["status"] == "filled"


def test_reconcile_endpoint_ok(broker_client):
    resp = broker_client.get("/paper/reconcile", params={"symbol": "VOO"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "VOO"
    assert body["ok"] is True
    assert body["position"]["qty"] == "1"
    assert body["buying_power"]["expected_change"] == "-684.65"
    names = {c["name"] for c in body["checks"]}
    assert {"order_filled", "cost_basis_consistent", "position_present"} <= names


def test_reconcile_with_baseline_computes_actual_delta(broker_client):
    resp = broker_client.get(
        "/paper/reconcile",
        params={"symbol": "VOO", "baseline_buying_power": "3999994.81"},
    )
    assert resp.status_code == 200
    bp = resp.json()["buying_power"]
    assert bp["baseline"] == "3999994.81"
    assert float(bp["actual_change"]) < 0  # a buy reduced buying power


def test_reconcile_requires_an_identifier(broker_client):
    resp = broker_client.get("/paper/reconcile")
    assert resp.status_code == 422


def test_reconcile_unknown_symbol_404(broker_client):
    resp = broker_client.get("/paper/reconcile", params={"symbol": "TSLA"})
    assert resp.status_code == 404
