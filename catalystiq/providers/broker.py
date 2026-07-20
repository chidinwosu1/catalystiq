"""BrokerProvider interface (§1.1 Execution Zone) and WebullBroker, the sole
active broker implementation
(https://developer.webull.com/apis/docs/trade-api/getting-started).

The active broker flow is always:

    Catalyst IQ backend -> BrokerProvider -> WebullBroker -> Webull Trading API

`get_broker_provider()` below only ever constructs `WebullBroker`; there is
no runtime broker selection and no fallback to any other provider. See its
docstring for the exact failure behavior when Webull isn't configured.

`AlpacaPaperBroker` remains in this module as a disabled legacy adapter -
it predates the Webull integration and is kept only because
`tests/test_broker_provider.py` still exercises its own field-mapping logic
in isolation. It is never constructed by `get_broker_provider()`, never
initialized as part of the normal application flow, and not reachable from
any router or the frontend.
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod

from catalystiq.providers.base import DataDomain
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
    """Disabled legacy adapter, backed by Alpaca's paper-trading environment.

    Not part of the active application flow: `get_broker_provider()` never
    constructs this class, and nothing in the routers, scheduler, or
    frontend references it. It's kept only so its own field-mapping logic
    stays covered by `tests/test_broker_provider.py`.
    """

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
            last_equity=str(account.last_equity),
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

    @staticmethod
    def _bracket_kwargs(order: NewOrder) -> dict:
        """Maps take_profit_price/stop_loss_price to Alpaca's order_class + leg
        requests (verified against alpaca-py's real request models: every
        order request type accepts order_class/take_profit/stop_loss, and
        TakeProfitRequest/StopLossRequest take limit_price/stop_price)."""
        from alpaca.trading.enums import OrderClass
        from alpaca.trading.requests import StopLossRequest, TakeProfitRequest

        has_tp = order.take_profit_price is not None
        has_sl = order.stop_loss_price is not None
        if not has_tp and not has_sl:
            return {}

        kwargs: dict = {}
        if has_tp:
            kwargs["take_profit"] = TakeProfitRequest(limit_price=order.take_profit_price)
        if has_sl:
            kwargs["stop_loss"] = StopLossRequest(stop_price=order.stop_loss_price)
        kwargs["order_class"] = OrderClass.BRACKET if (has_tp and has_sl) else OrderClass.OTO
        return kwargs

    def submit_order(self, order: NewOrder) -> dict:
        from alpaca.trading.requests import (
            LimitOrderRequest,
            MarketOrderRequest,
            StopLimitOrderRequest,
            StopOrderRequest,
            TrailingStopOrderRequest,
        )

        common = {
            "symbol": order.symbol.upper(),
            "side": self._order_side(order.side),
            "time_in_force": self._time_in_force(order.time_in_force),
            "extended_hours": order.extended_hours,
            **self._bracket_kwargs(order),
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
        elif order.type == "stop_limit":
            request = StopLimitOrderRequest(
                **common,
                stop_price=order.stop_price,
                limit_price=order.limit_price,
            )
        else:
            request = TrailingStopOrderRequest(
                **common,
                trail_percent=order.trail_percent,
                trail_price=order.trail_price,
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

    Account balance (`GET /openapi/assets/balance`) and positions
    (`account_v2.get_account_position`) are mapped by `_map_webull_account` /
    `_map_webull_positions`, with field names verified against a live sandbox
    account. The raw passthroughs (`get_account_balance_raw` /
    `get_positions_raw`) remain for inspection.

    Also NOT supported here (raises `BrokerError` rather than silently
    mismapping): notional (dollar-amount) orders - Webull's order schema is
    quantity-only in every example seen; extended-hours orders - the
    non-CORE `support_trading_session` value isn't confirmed; and `fok`
    time-in-force - Webull's `OrderTIF` enum only has DAY/GTC/IOC.
    """

    # Provider identity, per the ProviderAdapter contract
    # (catalystiq/providers/base.py).
    PROVIDER_NAME = "webull"
    ADAPTER_VERSION = "1.0.0"
    DOMAIN = DataDomain.BROKERAGE

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
        # Strip surrounding whitespace: credentials pasted into a hosting
        # dashboard (e.g. Render) very commonly pick up a trailing newline or
        # space. The request signature is an HMAC over the app_key + secret, so
        # a stray whitespace char silently produces "Header x-signature is
        # invalid" (HTTP 401) from Webull rather than an obvious config error.
        app_key = (app_key or "").strip()
        app_secret = (app_secret or "").strip()
        account_id = (account_id or "").strip()
        region_id = (region_id or "us").strip() or "us"
        # add_endpoint expects a BARE host (no scheme, no path). A pasted
        # "https://api.sandbox.webull.com" is signed as the host, which mismatches
        # what Webull signs (bare host) and yields "x-signature is invalid".
        api_endpoint = _normalize_webull_host(api_endpoint)
        token_dir = (token_dir or "").strip()

        if not app_key or not app_secret or not account_id:
            raise BrokerError("Webull app_key, app_secret, and account_id are not configured.")

        self._account_id = account_id
        self._market = self._MARKET_BY_REGION.get(region_id, region_id.upper())

        # The SDK import + client construction can fail in ways that are NOT
        # BrokerError (an ImportError if the SDK is missing, or an SDK/network
        # exception - TradeClient's constructor makes a network token/config
        # call). Wrap them so any construction failure surfaces as a clean 502
        # with a real reason, instead of an unhandled 500 that bypasses CORS and
        # shows in the browser as a misleading "Could not reach the API".
        try:
            from webull.core.client import ApiClient
            from webull.trade.trade_client import TradeClient

            api_client = ApiClient(app_key, app_secret, region_id)
            if api_endpoint:
                api_client.add_endpoint(region_id, api_endpoint)
            if token_dir:
                api_client.set_token_dir(token_dir)
            self._trade_client = TradeClient(api_client)
        except BrokerError:
            raise
        except Exception as exc:
            raise BrokerError(f"Failed to initialize the Webull client: {exc}") from exc

    @staticmethod
    def _check_response(response, not_found: bool = False):
        if response.status_code != 200:
            detail = f"Webull API error {response.status_code}: {response.text}"
            if not_found:
                raise OrderNotFoundError(detail)
            raise BrokerError(detail)
        return response.json()

    def get_account(self) -> AccountInfo:
        return _map_webull_account(self.get_account_balance_raw())

    def get_account_balance_raw(self) -> dict:
        """Unmapped passthrough of GET /openapi/assets/balance."""
        response = self._trade_client.account_v2.get_account_balance(self._account_id)
        return self._check_response(response)

    def get_positions(self) -> list[Position]:
        return _map_webull_positions(self.get_positions_raw())

    def get_positions_raw(self):
        """Unmapped passthrough of account_v2.get_account_position (a JSON list)."""
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

    def connection_test(self) -> dict:
        """Read-only reachability check (§13): performs a lightweight open-
        orders read and reports ok/failure without exposing any credential.
        Never places, cancels, or modifies an order."""
        try:
            self._trade_client.order_v3.get_order_open(self._account_id)
            return {"provider": "webull", "ok": True, "detail": "reachable (read-only)"}
        except BrokerError as exc:
            return {"provider": "webull", "ok": False, "detail": str(exc)}
        except Exception as exc:  # pragma: no cover - network/library errors
            return {"provider": "webull", "ok": False, "detail": f"{type(exc).__name__}"}

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
        if order.type not in self._ORDER_TYPE:
            raise BrokerError(
                f"order_type={order.type!r} isn't mapped for Webull yet "
                "(only market/limit/stop/stop_limit)."
            )
        if order.take_profit_price is not None or order.stop_loss_price is not None:
            raise BrokerError(
                "Bracket/take-profit/stop-loss legs aren't supported for Webull orders "
                "in this integration - Webull's combo/bracket order shape isn't verified."
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


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _num_str(value, default: str = "0") -> str:
    """Webull returns numbers as strings; pass them through, defaulting a
    missing/None value rather than emitting 'None'."""
    return default if value is None else str(value)


def _map_webull_account(data: dict) -> AccountInfo:
    """Map Webull's /openapi/assets/balance response (verified against a live
    sandbox account) to the provider-agnostic AccountInfo. Webull reports totals
    at the top level and per-currency buying power under
    `account_currency_assets`; it has no explicit status/blocked/PDT flags, so
    those use safe defaults (an open margin call is the one restriction it does
    report)."""
    currency = str(data.get("total_asset_currency") or "USD")
    assets = data.get("account_currency_assets") or []
    asset = next(
        (a for a in assets if str(a.get("currency")) == currency),
        assets[0] if assets else {},
    )

    net_liq = data.get("total_net_liquidation_value")
    day_pl = data.get("total_day_profit_loss")
    # Yesterday's close equity = today's equity minus today's P/L (both real).
    net_liq_f, day_pl_f = _to_float(net_liq), _to_float(day_pl)
    if net_liq_f is not None and day_pl_f is not None:
        last_equity = str(round(net_liq_f - day_pl_f, 2))
    else:
        last_equity = _num_str(net_liq)

    margin_calls = data.get("open_margin_calls") or []

    return AccountInfo(
        status="ACTIVE",
        currency=currency,
        cash=_num_str(data.get("total_cash_balance") or asset.get("cash_balance")),
        # Day-trading buying power (Webull's headline BP), falling back to
        # overnight then cash if a plan doesn't report it.
        buying_power=_num_str(
            asset.get("day_buying_power")
            or asset.get("overnight_buying_power")
            or data.get("total_cash_balance")
        ),
        portfolio_value=_num_str(net_liq),
        equity=_num_str(net_liq),
        last_equity=last_equity,
        trading_blocked=bool(margin_calls),
        account_blocked=False,
        pattern_day_trader=False,
    )


def _map_webull_positions(data) -> list[Position]:
    """Map Webull's account-position response (a JSON list, verified against a
    live sandbox account) to provider-agnostic Positions. Tolerates a
    dict-wrapped list too. Side is derived from quantity sign."""
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("positions") or data.get("items") or []
    else:
        rows = []

    out: list[Position] = []
    for p in rows:
        qty = p.get("quantity")
        qty_f = _to_float(qty)
        side = "short" if (qty_f is not None and qty_f < 0) else "long"
        out.append(
            Position(
                symbol=str(p.get("symbol") or ""),
                side=side,
                qty=_num_str(qty),
                avg_entry_price=_num_str(p.get("cost_price")),
                market_value=_num_str(p.get("market_value")),
                cost_basis=_num_str(p.get("cost")),
                unrealized_pl=_num_str(p.get("unrealized_profit_loss")),
                unrealized_plpc=_num_str(p.get("unrealized_profit_loss_rate")),
                current_price=_num_str(p.get("last_price")),
                change_today=_num_str(p.get("day_profit_loss")),
            )
        )
    return out


_SUPPORTED_BROKER_PROVIDERS = {"webull"}


def get_broker_provider() -> BrokerProvider:
    """Factory returning the active BrokerProvider - always WebullBroker.

    This runs as a FastAPI dependency, so a BrokerError raised here (either
    for an unsupported BROKER_PROVIDER value or missing Webull credentials)
    is caught by the app-level `@app.exception_handler(BrokerError)` and
    turned into a clean 502 JSON response - never an unhandled 500, and
    never a silent fallback to any other broker.
    """
    from catalystiq.config import get_settings

    settings = get_settings()
    if settings.broker_provider not in _SUPPORTED_BROKER_PROVIDERS:
        raise BrokerError(
            f"Unsupported BROKER_PROVIDER={settings.broker_provider!r}. "
            f"Only {sorted(_SUPPORTED_BROKER_PROVIDERS)} is supported - Webull is the "
            "sole active broker and there is no fallback to any other provider."
        )

    return WebullBroker(
        settings.webull_app_key,
        settings.webull_app_secret,
        settings.webull_account_id,
        region_id=settings.webull_region_id,
        api_endpoint=settings.webull_api_base_url,
        token_dir=settings.webull_token_dir,
    )


def _normalize_webull_host(value: str | None) -> str:
    """Reduce a configured base URL to the bare host the Webull SDK's
    add_endpoint expects: no scheme, no path, no trailing dots/slashes.
    "https://api.sandbox.webull.com/" -> "api.sandbox.webull.com". Empty in,
    empty out (the SDK then uses its default host)."""
    v = (value or "").strip()
    if not v:
        return ""
    v = v.split("://", 1)[-1]  # drop scheme (https:// / http://)
    v = v.split("/", 1)[0]  # drop any path
    return v.strip().strip(".")


def _sdk_version() -> str:
    try:
        import webull

        return getattr(webull, "__version__", "unknown")
    except Exception:  # noqa: BLE001
        return "unknown"


def _mask(value: str | None) -> dict:
    """Secret-masked view of a credential: whether it's set, its length, and a
    short preview - never the raw value. Length exposes accidental
    truncation/whitespace; the preview helps confirm the right value is set."""
    v = (value or "").strip()
    if not v:
        return {"set": False, "length": 0, "preview": ""}
    preview = v if len(v) <= 8 else f"{v[:4]}…{v[-4:]}"
    return {"set": True, "length": len(v), "preview": preview}


def webull_diagnostics() -> dict:
    """Read-only, secret-masked snapshot of the Webull configuration for
    debugging auth/signature failures: the host the SDK actually resolves to,
    the (masked) credentials in use, and the real initialization error. Never
    returns raw secrets and never places an order."""
    from catalystiq.config import get_settings

    settings = get_settings()
    region = (settings.webull_region_id or "us").strip() or "us"
    raw_base_url = (settings.webull_api_base_url or "").strip()
    base_url = _normalize_webull_host(raw_base_url)

    out: dict = {
        "broker_provider": settings.broker_provider,
        "region_id": region,
        "api_base_url_setting": raw_base_url,
        "api_base_url_normalized": base_url,
        "app_key": _mask(settings.webull_app_key),
        # Never preview a secret - length only (catches whitespace/truncation).
        "app_secret": {
            "set": bool((settings.webull_app_secret or "").strip()),
            "length": len((settings.webull_app_secret or "").strip()),
        },
        "account_id": _mask(settings.webull_account_id),
        "sdk_version": _sdk_version(),
        "resolved_trade_host": None,
        "signer": "HMAC-SHA256 (app_secret) - RSA not used",
        "init_ok": False,
        "init_error": None,
    }

    # Resolve the host the trade calls will hit - WITHOUT a network call - so we
    # can confirm the sandbox override actually took effect.
    try:
        from webull.core.client import ApiClient
        from webull.core.common import api_type as _api_type
        from webull.core.endpoint.resolver_endpoint_request import ResolveEndpointRequest

        api_client = ApiClient(
            (settings.webull_app_key or "").strip(),
            (settings.webull_app_secret or "").strip(),
            region,
        )
        if base_url:
            api_client.add_endpoint(region, base_url)
        request = ResolveEndpointRequest(region, _api_type.DEFAULT)
        out["resolved_trade_host"] = api_client._endpoint_resolver.resolve(request)
    except Exception as exc:  # noqa: BLE001 - diagnostic, report the reason
        out["resolve_error"] = f"{type(exc).__name__}: {exc}"

    # Attempt a full init (this DOES network) to capture the real error text.
    try:
        get_broker_provider()
        out["init_ok"] = True
    except Exception as exc:  # noqa: BLE001 - diagnostic surfaces the message
        out["init_error"] = str(exc)

    return out
