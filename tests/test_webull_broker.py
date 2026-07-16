"""WebullBroker tests. WebullBroker.__init__ makes a real network call (the
SDK's TradeClient constructor checks the 2FA token/config), so tests build
instances via object.__new__ and set internals directly rather than calling
__init__ - this also means these tests don't require the webull SDK package
to be installed to run, matching the rest of this suite's offline pattern.
"""
from unittest.mock import MagicMock

import pytest

from catalystiq.providers.broker import BrokerError, OrderNotFoundError, WebullBroker
from catalystiq.schemas.broker import NewOrder


def make_broker(trade_client=None) -> WebullBroker:
    broker = object.__new__(WebullBroker)
    broker._account_id = "test-account"
    broker._market = "US"
    broker._trade_client = trade_client or MagicMock()
    return broker


def fake_response(status_code=200, json_body=None, text=""):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body if json_body is not None else {}
    response.text = text
    return response


def test_missing_credentials_raise_broker_error():
    with pytest.raises(BrokerError):
        WebullBroker("", "", "")
    with pytest.raises(BrokerError):
        WebullBroker("key", "secret", "")


def test_get_account_raises_pointing_at_raw_method():
    broker = make_broker()
    with pytest.raises(BrokerError, match="get_account_balance_raw"):
        broker.get_account()


def test_get_positions_raises_pointing_at_raw_method():
    broker = make_broker()
    with pytest.raises(BrokerError, match="get_positions_raw"):
        broker.get_positions()


def test_get_account_balance_raw_returns_json_on_success():
    trade_client = MagicMock()
    trade_client.account_v2.get_account_balance.return_value = fake_response(
        json_body={"whatever": "webull sends"}
    )
    broker = make_broker(trade_client)

    result = broker.get_account_balance_raw()

    assert result == {"whatever": "webull sends"}
    trade_client.account_v2.get_account_balance.assert_called_once_with("test-account")


def test_get_account_balance_raw_wraps_error_status():
    trade_client = MagicMock()
    trade_client.account_v2.get_account_balance.return_value = fake_response(
        status_code=401, text="unauthorized"
    )
    broker = make_broker(trade_client)

    with pytest.raises(BrokerError, match="401"):
        broker.get_account_balance_raw()


def test_get_positions_raw_returns_json_on_success():
    trade_client = MagicMock()
    trade_client.account_v2.get_account_position.return_value = fake_response(
        json_body={"positions": []}
    )
    broker = make_broker(trade_client)

    assert broker.get_positions_raw() == {"positions": []}


def test_get_orders_returns_json():
    trade_client = MagicMock()
    trade_client.order_v3.get_order_open.return_value = fake_response(json_body=[{"id": "1"}])
    broker = make_broker(trade_client)

    assert broker.get_orders() == [{"id": "1"}]


def test_submit_order_market_buy_maps_correctly():
    trade_client = MagicMock()
    trade_client.order_v3.place_order.return_value = fake_response(json_body={"status": "ok"})
    broker = make_broker(trade_client)

    order = NewOrder(symbol="aapl", side="buy", type="market", qty=5)
    result = broker.submit_order(order)

    assert result == {"status": "ok"}
    args, _ = trade_client.order_v3.place_order.call_args
    account_id, webull_orders = args
    assert account_id == "test-account"
    mapped = webull_orders[0]
    assert mapped["symbol"] == "AAPL"
    assert mapped["side"] == "BUY"
    assert mapped["order_type"] == "MARKET"
    assert mapped["quantity"] == "5.0"
    assert mapped["market"] == "US"
    assert mapped["entrust_type"] == "QTY"
    assert mapped["time_in_force"] == "DAY"
    assert "limit_price" not in mapped


def test_submit_order_limit_sell_includes_limit_price():
    trade_client = MagicMock()
    trade_client.order_v3.place_order.return_value = fake_response(json_body={"status": "ok"})
    broker = make_broker(trade_client)

    order = NewOrder(
        symbol="msft", side="sell", type="limit", qty=2, limit_price=410.5, time_in_force="gtc"
    )
    broker.submit_order(order)

    mapped = trade_client.order_v3.place_order.call_args[0][1][0]
    assert mapped["order_type"] == "LIMIT"
    assert mapped["side"] == "SELL"
    assert mapped["limit_price"] == "410.5"
    assert mapped["time_in_force"] == "GTC"


def test_submit_order_stop_limit_includes_both_prices():
    trade_client = MagicMock()
    trade_client.order_v3.place_order.return_value = fake_response(json_body={"status": "ok"})
    broker = make_broker(trade_client)

    order = NewOrder(
        symbol="tsla",
        side="buy",
        type="stop_limit",
        qty=1,
        stop_price=250,
        limit_price=252,
    )
    broker.submit_order(order)

    mapped = trade_client.order_v3.place_order.call_args[0][1][0]
    assert mapped["order_type"] == "STOP_LOSS_LIMIT"
    assert mapped["stop_price"] == "250.0"
    assert mapped["limit_price"] == "252.0"


def test_submit_order_uses_provided_client_order_id():
    trade_client = MagicMock()
    trade_client.order_v3.place_order.return_value = fake_response(json_body={"status": "ok"})
    broker = make_broker(trade_client)

    order = NewOrder(
        symbol="aapl", side="buy", type="market", qty=1, client_order_id="my-id-123"
    )
    broker.submit_order(order)

    mapped = trade_client.order_v3.place_order.call_args[0][1][0]
    assert mapped["client_order_id"] == "my-id-123"


def test_submit_order_notional_not_supported():
    broker = make_broker()
    order = NewOrder(symbol="aapl", side="buy", type="market", notional=100)

    with pytest.raises(BrokerError, match="notional"):
        broker.submit_order(order)


def test_submit_order_extended_hours_not_supported():
    broker = make_broker()
    order = NewOrder(symbol="aapl", side="buy", type="market", qty=1, extended_hours=True)

    with pytest.raises(BrokerError, match="Extended-hours"):
        broker.submit_order(order)


def test_submit_order_fok_not_supported():
    broker = make_broker()
    order = NewOrder(symbol="aapl", side="buy", type="market", qty=1, time_in_force="fok")

    with pytest.raises(BrokerError, match="time_in_force"):
        broker.submit_order(order)


def test_get_order_not_found_raises_order_not_found_error():
    trade_client = MagicMock()
    trade_client.order_v3.get_order_detail.return_value = fake_response(
        status_code=404, text="not found"
    )
    broker = make_broker(trade_client)

    with pytest.raises(OrderNotFoundError):
        broker.get_order("missing-id")


def test_get_order_success():
    trade_client = MagicMock()
    trade_client.order_v3.get_order_detail.return_value = fake_response(
        json_body={"client_order_id": "abc"}
    )
    broker = make_broker(trade_client)

    assert broker.get_order("abc") == {"client_order_id": "abc"}
    trade_client.order_v3.get_order_detail.assert_called_once_with("test-account", "abc")


def test_cancel_order_success():
    trade_client = MagicMock()
    trade_client.order_v3.cancel_order.return_value = fake_response(json_body={"ok": True})
    broker = make_broker(trade_client)

    broker.cancel_order("abc")

    trade_client.order_v3.cancel_order.assert_called_once_with("test-account", "abc")


def test_cancel_order_wraps_error_status():
    trade_client = MagicMock()
    trade_client.order_v3.cancel_order.return_value = fake_response(
        status_code=422, text="cannot cancel filled order"
    )
    broker = make_broker(trade_client)

    with pytest.raises(BrokerError, match="422"):
        broker.cancel_order("abc")
