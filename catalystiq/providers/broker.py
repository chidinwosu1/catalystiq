"""BrokerProvider interface (§1.1 Execution Zone), the Alpaca paper-trading
implementation, and a Webull OpenAPI implementation
(https://developer.webull.com/apis/docs/trade-api/getting-started).

The build spec's execution zone always targeted Webull's paper-trading
endpoint; Alpaca was this codebase's original, already-working integration,
kept as the default BrokerProvider behind this same interface. WebullBroker
below is a real integration against the official `webull-openapi-python-sdk`
- see its docstring for what is and isn't verified.
"""
from __future__ import annotations

import uuid
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


class WebullBroker(BrokerProvider):
    """BrokerProvider backed by Webull's OpenAPI trade endpoints.

    Verified against the real `webull-openapi-python-sdk` (PyPI) source and
    Webull's own Trading API "Getting Started" guide: `ApiClient(app_key,
    app_secret, region_id)` + `TradeClient(api_client)`, `account_v2.*` for
    account/position reads, `order_v3.*` for the order lifecycle
    (`/openapi/trade/order/...`). Order field names and the OrderType/OrderTIF
    enums below are taken directly from that SDK source, not guessed.

    NOT verified: the JSON response *shape* for account balance
    (`GET /openapi/assets/balance`) and positions (`account_v2.get_account_position`)
    - the SDK ships no response models for these (`get_response()` returns
    the raw HTTP response) and this build couldn't fetch Webull's Accounts
    reference page to confirm field names. Rather than guess field names and
    silently show wrong numbers, `get_account()`/`get_positions()` raise
    `BrokerError` pointing at the raw passthrough methods instead. Wire up a
    typed mapping once those field names are confirmed (e.g. against a live
    sandbox call, or the Accounts reference page content).

    Also NOT supported here (raises `BrokerError` rather than silently
    mismapping): notional (dollar-amount) orders - Webull's order schema is
    quantity-only in every example seen; extended-hours orders - the
    non-CORE `support_trading_session` value isn't confirmed; and `fok`
    time-in-force - Webull's `OrderTIF` enum only has DAY/GTC/IOC.
    """

    # From webull.trade.common.order_type.OrderType / order_tif.OrderTIF.
    _ORDER_TYPE = {
        "market": "MARKET",
        "limit": "LIMIT",
        "stop": "STOP_LOSS",
        "stop_limit": "STOP_LOSS_LIMIT",
    }
    _TIME_IN_FORCE = {
        "day": "DAY",
        "gtc": "GTC",
        "ioc": "IOC",
    }
    _MARKET_BY_REGION = {
        "us": "US",
        "hk": "HK",
    }

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        account_id: str,
        region_id: str = "us",
        api_endpoint: str = "",
        token_dir: str = "",
    ) -> None:
        if not app_key or not app_secret or not account_id:
            raise BrokerError("Webull app_key, app_secret, and account_id are not configured.")

        from webull.core.client import ApiClient
        from webull.trade.trade_client import TradeClient

        self._account_id = account_id
        self._market = self._MARKET_BY_REGION.get(region_id, region_id.upper())

        api_client = ApiClient(app_key, app_secret, region_id)
        if api_endpoint:
            api_client.add_endpoint(region_id, api_endpoint)
        if token_dir:
            api_client.set_token_dir(token_dir)

        # TradeClient's constructor itself makes a network call (token/config
        # check), so it can't be built lazily per-call the way the Yahoo/Alpaca
        # clients are - it happens once, here, at broker construction.
        self._trade_client = TradeClient(api_client)

    @staticmethod
    def _check_response(response, not_found: bool = False):
        if response.status_code != 200:
            detail = f"Webull API error {response.status_code}: {response.text}"
            if not_found:
                raise OrderNotFoundError(detail)
            raise BrokerError(detail)
        return response.json()

    def get_account(self) -> AccountInfo:
        raise BrokerError(
            "WebullBroker.get_account() has no verified field mapping for Webull's "
            "balance response - see the class docstring. Use get_account_balance_raw() "
            "for the unmapped JSON."
        )

    def get_account_balance_raw(self) -> dict:
        """Unmapped passthrough of GET /openapi/assets/balance."""
        response = self._trade_client.account_v2.get_account_balance(self._account_id)
        return self._check_response(response)

    def get_positions(self) -> list[Position]:
        raise BrokerError(
            "WebullBroker.get_positions() has no verified field mapping for Webull's "
            "positions response - see the class docstring. Use get_positions_raw() for "
            "the unmapped JSON."
        )

    def get_positions_raw(self) -> dict:
        """Unmapped passthrough of account_v2.get_account_position."""
        response = self._trade_client.account_v2.get_account_position(self._account_id)
        return self._check_response(response)

    def get_orders(self) -> list[dict]:
        # Response shape (bare list vs. a dict-wrapped list) isn't confirmed
        # either - unlike get_account()/get_positions() this is a raw
        # passthrough with no asserted field names, so it's returned as-is
        # rather than blocked, but callers should check the actual shape
        # against a live response before assuming list[dict].
        response = self._trade_client.order_v3.get_order_open(self._account_id)
        return self._check_response(response)

    def submit_order(self, order: NewOrder) -> dict:
        webull_order = self._to_webull_order(order)
        response = self._trade_client.order_v3.place_order(self._account_id, [webull_order])
        return self._check_response(response)

    def get_order(self, order_id: str) -> dict:
        response = self._trade_client.order_v3.get_order_detail(self._account_id, order_id)
        return self._check_response(response, not_found=True)

    def cancel_order(self, order_id: str) -> None:
        response = self._trade_client.order_v3.cancel_order(self._account_id, order_id)
        self._check_response(response)

    def _to_webull_order(self, order: NewOrder) -> dict:
        if order.notional is not None:
            raise BrokerError(
                "Webull orders are quantity-only in this integration - notional "
                "(dollar-amount) orders aren't supported."
            )
        if order.extended_hours:
            raise BrokerError(
                "Extended-hours orders aren't supported - the non-CORE "
                "support_trading_session value isn't verified."
            )
        if order.time_in_force not in self._TIME_IN_FORCE:
            raise BrokerError(
                f"Webull doesn't support time_in_force={order.time_in_force!r} "
                "(only day/gtc/ioc)."
            )

        webull_order = {
            "combo_type": "NORMAL",
            "client_order_id": order.client_order_id or uuid.uuid4().hex,
            "symbol": order.symbol.upper(),
            "instrument_type": "EQUITY",
            "market": self._market,
            "order_type": self._ORDER_TYPE[order.type],
            "quantity": str(order.qty),
            "support_trading_session": "CORE",
            "side": "BUY" if order.side == "buy" else "SELL",
            "time_in_force": self._TIME_IN_FORCE[order.time_in_force],
            "entrust_type": "QTY",
        }
        if order.limit_price is not None:
            webull_order["limit_price"] = str(order.limit_price)
        if order.stop_price is not None:
            webull_order["stop_price"] = str(order.stop_price)
        return webull_order


def get_broker_provider() -> BrokerProvider:
    """Factory returning the configured BrokerProvider (§config.broker_provider)."""
    from catalystiq.config import get_settings

    settings = get_settings()
    if settings.broker_provider == "webull":
        return WebullBroker(
            settings.webull_app_key,
            settings.webull_app_secret,
            settings.webull_account_id,
            region_id=settings.webull_region_id,
            api_endpoint=settings.webull_api_endpoint,
            token_dir=settings.webull_token_dir,
        )
    return AlpacaPaperBroker(settings.alpaca_api_key, settings.alpaca_secret_key)
