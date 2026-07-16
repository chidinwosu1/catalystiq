"""AlpacaPaperBroker mapping tests, using a mocked alpaca TradingClient - no network."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from catalystiq.providers.broker import (
    AlpacaPaperBroker,
    BrokerError,
    OrderNotFoundError,
)
from catalystiq.schemas.broker import NewOrder


@pytest.fixture
def broker():
    b = AlpacaPaperBroker("dummy-key", "dummy-secret")
    b._client = MagicMock()
    return b


def test_missing_credentials_raise_broker_error():
    with pytest.raises(BrokerError):
        AlpacaPaperBroker("", "")


def test_get_account_maps_fields(broker):
    broker._client.get_account.return_value = SimpleNamespace(
        status="ACTIVE",
        currency="USD",
        cash="1000.00",
        buying_power="2000.00",
        portfolio_value="1500.00",
        equity="1500.00",
        last_equity="1480.00",
        trading_blocked=False,
        account_blocked=False,
        pattern_day_trader=False,
    )

    account = broker.get_account()

    assert account.status == "ACTIVE"
    assert account.cash == "1000.00"
    assert account.trading_blocked is False


def test_get_account_wraps_exceptions(broker):
    broker._client.get_account.side_effect = RuntimeError("network down")

    with pytest.raises(BrokerError):
        broker.get_account()


def test_get_positions_maps_list(broker):
    broker._client.get_all_positions.return_value = [
        SimpleNamespace(
            symbol="AAPL",
            side="long",
            qty="10",
            avg_entry_price="150.00",
            market_value="1600.00",
            cost_basis="1500.00",
            unrealized_pl="100.00",
            unrealized_plpc="0.0667",
            current_price="160.00",
            change_today="0.01",
        )
    ]

    positions = broker.get_positions()

    assert len(positions) == 1
    assert positions[0].symbol == "AAPL"
    assert positions[0].current_price == "160.00"


def test_submit_order_market_buy_builds_correct_request(broker):
    fake_result = MagicMock()
    fake_result.model_dump.return_value = {"id": "order-1", "status": "accepted"}
    broker._client.submit_order.return_value = fake_result

    order = NewOrder(symbol="aapl", side="buy", type="market", qty=5)
    result = broker.submit_order(order)

    assert result == {"id": "order-1", "status": "accepted"}
    call_kwargs = broker._client.submit_order.call_args.kwargs
    request = call_kwargs["order_data"]
    assert request.symbol == "AAPL"
    assert request.qty == 5


def test_submit_order_wraps_rejection_as_broker_error(broker):
    broker._client.submit_order.side_effect = RuntimeError("insufficient buying power")
    order = NewOrder(symbol="aapl", side="buy", type="market", qty=5)

    with pytest.raises(BrokerError):
        broker.submit_order(order)


def test_get_order_not_found_raises_order_not_found_error(broker):
    broker._client.get_order_by_id.side_effect = RuntimeError("404")

    with pytest.raises(OrderNotFoundError):
        broker.get_order("missing-id")


def test_cancel_order_wraps_exceptions(broker):
    broker._client.cancel_order_by_id.side_effect = RuntimeError("boom")

    with pytest.raises(BrokerError):
        broker.cancel_order("some-id")


def test_new_order_requires_qty_xor_notional():
    with pytest.raises(ValueError):
        NewOrder(symbol="aapl", side="buy", type="market", qty=5, notional=100)
    with pytest.raises(ValueError):
        NewOrder(symbol="aapl", side="buy", type="market")


def test_new_order_limit_requires_limit_price():
    with pytest.raises(ValueError):
        NewOrder(symbol="aapl", side="buy", type="limit", qty=5)


def test_new_order_trailing_stop_requires_exactly_one_trail_field():
    with pytest.raises(ValueError):
        NewOrder(symbol="aapl", side="buy", type="trailing_stop", qty=1)
    with pytest.raises(ValueError):
        NewOrder(
            symbol="aapl",
            side="buy",
            type="trailing_stop",
            qty=1,
            trail_percent=5,
            trail_price=2,
        )
    # exactly one is fine
    NewOrder(symbol="aapl", side="buy", type="trailing_stop", qty=1, trail_percent=5)


def test_new_order_trail_fields_only_valid_for_trailing_stop():
    with pytest.raises(ValueError):
        NewOrder(symbol="aapl", side="buy", type="market", qty=1, trail_percent=5)


def test_submit_order_trailing_stop_builds_correct_request(broker):
    fake_result = MagicMock()
    fake_result.model_dump.return_value = {"id": "order-2", "status": "accepted"}
    broker._client.submit_order.return_value = fake_result

    order = NewOrder(
        symbol="tsla", side="sell", type="trailing_stop", qty=3, trail_percent=4.5
    )
    broker.submit_order(order)

    request = broker._client.submit_order.call_args.kwargs["order_data"]
    assert request.trail_percent == 4.5
    assert request.trail_price is None


def test_submit_order_with_take_profit_and_stop_loss_builds_bracket(broker):
    from alpaca.trading.enums import OrderClass

    fake_result = MagicMock()
    fake_result.model_dump.return_value = {"id": "order-3", "status": "accepted"}
    broker._client.submit_order.return_value = fake_result

    order = NewOrder(
        symbol="nvda",
        side="buy",
        type="market",
        qty=10,
        take_profit_price=220,
        stop_loss_price=190,
    )
    broker.submit_order(order)

    request = broker._client.submit_order.call_args.kwargs["order_data"]
    assert request.order_class == OrderClass.BRACKET
    assert request.take_profit.limit_price == 220
    assert request.stop_loss.stop_price == 190


def test_submit_order_with_only_stop_loss_builds_oto(broker):
    from alpaca.trading.enums import OrderClass

    fake_result = MagicMock()
    fake_result.model_dump.return_value = {"id": "order-4", "status": "accepted"}
    broker._client.submit_order.return_value = fake_result

    order = NewOrder(symbol="nvda", side="buy", type="market", qty=10, stop_loss_price=190)
    broker.submit_order(order)

    request = broker._client.submit_order.call_args.kwargs["order_data"]
    assert request.order_class == OrderClass.OTO
    assert request.stop_loss.stop_price == 190
    assert request.take_profit is None
