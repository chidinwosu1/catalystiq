"""BrokerProvider interface (§1.1 Execution Zone) and the Alpaca paper-trading implementation.

The build spec's execution zone talks to Webull's paper-trading endpoint;
this codebase's existing, working paper-trading integration is Alpaca, which
exposes the same conceptual surface (account, positions, orders, fills,
cash/buying power). It's implemented behind this interface so it can be
swapped for a Webull-backed provider later without touching routers or
callers.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from catalystiq.schemas.broker import AccountInfo, NewOrder, Position


class BrokerError(RuntimeError):
    """Raised when a broker call fails (auth, network, or rejected order)."""


class OrderNotFoundError(BrokerError):
    """Raised when an order id doesn't exist at the broker."""


class BrokerProvider(ABC):
    """Abstract paper-trading broker: account, positions, and order lifecycle."""

    @abstractmethod
    def get_account(self) -> AccountInfo:
        ...

    @abstractmethod
    def get_positions(self) -> list[Position]:
        ...

    @abstractmethod
    def get_orders(self) -> list[dict]:
        ...

    @abstractmethod
    def submit_order(self, order: NewOrder) -> dict:
        ...

    @abstractmethod
    def get_order(self, order_id: str) -> dict:
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> None:
        ...


class AlpacaPaperBroker(BrokerProvider):
    """BrokerProvider backed by Alpaca's paper-trading environment."""

    def __init__(self, api_key: str, secret_key: str) -> None:
        if not api_key or not secret_key:
            raise BrokerError("Alpaca paper credentials are not configured.")

        from alpaca.trading.client import TradingClient

        self._client = TradingClient(api_key, secret_key, paper=True)

    @staticmethod
    def _order_side(value: str):
        from alpaca.trading.enums import OrderSide

        return OrderSide.BUY if value == "buy" else OrderSide.SELL

    @staticmethod
    def _time_in_force(value: str):
        from alpaca.trading.enums import TimeInForce

        values = {
            "day": TimeInForce.DAY,
            "gtc": TimeInForce.GTC,
            "ioc": TimeInForce.IOC,
            "fok": TimeInForce.FOK,
        }
        return values[value]

    def get_account(self) -> AccountInfo:
        try:
            account = self._client.get_account()
        except Exception as exc:  # pragma: no cover - network/library errors
            raise BrokerError(str(exc)) from exc

        return AccountInfo(
            status=str(account.status),
            currency=account.currency,
            cash=str(account.cash),
            buying_power=str(account.buying_power),
            portfolio_value=str(account.portfolio_value),
            equity=str(account.equity),
            trading_blocked=account.trading_blocked,
            account_blocked=account.account_blocked,
            pattern_day_trader=account.pattern_day_trader,
        )

    def get_positions(self) -> list[Position]:
        try:
            positions = self._client.get_all_positions()
        except Exception as exc:  # pragma: no cover - network/library errors
            raise BrokerError(str(exc)) from exc

        return [
            Position(
                symbol=position.symbol,
                side=str(position.side),
                qty=str(position.qty),
                avg_entry_price=str(position.avg_entry_price),
                market_value=str(position.market_value),
                cost_basis=str(position.cost_basis),
                unrealized_pl=str(position.unrealized_pl),
                unrealized_plpc=str(position.unrealized_plpc),
                current_price=str(position.current_price),
                change_today=str(position.change_today),
            )
            for position in positions
        ]

    def get_orders(self) -> list[dict]:
        try:
            orders = self._client.get_orders()
        except Exception as exc:  # pragma: no cover - network/library errors
            raise BrokerError(str(exc)) from exc

        return [order.model_dump(mode="json") for order in orders]

    def submit_order(self, order: NewOrder) -> dict:
        from alpaca.trading.requests import (
            LimitOrderRequest,
            MarketOrderRequest,
            StopLimitOrderRequest,
            StopOrderRequest,
        )

        common = {
            "symbol": order.symbol.upper(),
            "side": self._order_side(order.side),
            "time_in_force": self._time_in_force(order.time_in_force),
            "extended_hours": order.extended_hours,
        }

        if order.qty is not None:
            common["qty"] = order.qty
        else:
            common["notional"] = order.notional

        if order.client_order_id:
            common["client_order_id"] = order.client_order_id

        if order.type == "market":
            request = MarketOrderRequest(**common)
        elif order.type == "limit":
            request = LimitOrderRequest(**common, limit_price=order.limit_price)
        elif order.type == "stop":
            request = StopOrderRequest(**common, stop_price=order.stop_price)
        else:
            request = StopLimitOrderRequest(
                **common,
                stop_price=order.stop_price,
                limit_price=order.limit_price,
            )

        try:
            result = self._client.submit_order(order_data=request)
        except Exception as exc:
            raise BrokerError(str(exc)) from exc

        return result.model_dump(mode="json")

    def get_order(self, order_id: str) -> dict:
        try:
            order = self._client.get_order_by_id(order_id)
        except Exception as exc:
            raise OrderNotFoundError(str(exc)) from exc

        return order.model_dump(mode="json")

    def cancel_order(self, order_id: str) -> None:
        try:
            self._client.cancel_order_by_id(order_id)
        except Exception as exc:
            raise BrokerError(str(exc)) from exc


def get_broker_provider() -> BrokerProvider:
    """Factory returning the configured BrokerProvider."""
    from catalystiq.config import get_settings

    settings = get_settings()
    return AlpacaPaperBroker(settings.alpaca_api_key, settings.alpaca_secret_key)
