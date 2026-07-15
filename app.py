import os
from typing import Literal, Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    StopLimitOrderRequest,
    StopOrderRequest,
)
from fastapi import FastAPI, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, model_validator


app = FastAPI(
    title="Catalyst IQ Paper Trading API",
    version="1.0.0",
)
security = HTTPBearer()

def verify_action_key(
    credentials: HTTPAuthorizationCredentials,
) -> None:
    expected_key = os.getenv("ACTION_API_KEY", "").strip()

    if not expected_key:
        raise HTTPException(
            status_code=500,
            detail="ACTION_API_KEY is not configured.",
        )

    if credentials.credentials != expected_key:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized.",
        )

def get_trading_client() -> TradingClient:
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")

    if not api_key or not secret_key:
        raise HTTPException(
            status_code=500,
            detail="Alpaca paper credentials are not configured.",
        )

    return TradingClient(api_key, secret_key, paper=True)


class NewOrder(BaseModel):
    symbol: str = Field(min_length=1, max_length=15)
    side: Literal["buy", "sell"]
    type: Literal["market", "limit", "stop", "stop_limit"]
    time_in_force: Literal["day", "gtc", "ioc", "fok"] = "day"

    qty: Optional[float] = Field(default=None, gt=0)
    notional: Optional[float] = Field(default=None, gt=0, le=5000)
    limit_price: Optional[float] = Field(default=None, gt=0)
    stop_price: Optional[float] = Field(default=None, gt=0)
    extended_hours: bool = False
    client_order_id: Optional[str] = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_order(self):
        if (self.qty is None) == (self.notional is None):
            raise ValueError("Provide either qty or notional, but not both.")

        if self.type in {"limit", "stop_limit"} and self.limit_price is None:
            raise ValueError("limit_price is required.")

        if self.type in {"stop", "stop_limit"} and self.stop_price is None:
            raise ValueError("stop_price is required.")

        return self


def order_side(value: str) -> OrderSide:
    return OrderSide.BUY if value == "buy" else OrderSide.SELL


def time_in_force(value: str) -> TimeInForce:
    values = {
        "day": TimeInForce.DAY,
        "gtc": TimeInForce.GTC,
        "ioc": TimeInForce.IOC,
        "fok": TimeInForce.FOK,
    }
    return values[value]


@app.get("/")
def root():
    return {
        "status": "online",
        "service": "Catalyst IQ Paper Trading API",
        "paper_trading": True,
    }


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/paper/account")
def get_paper_account(
    credentials: HTTPAuthorizationCredentials = Security(security),
):
    verify_action_key(credentials)

    try:
        account = get_trading_client().get_account()
        return {
            "status": str(account.status),
            "currency": account.currency,
            "cash": str(account.cash),
            "buying_power": str(account.buying_power),
            "portfolio_value": str(account.portfolio_value),
            "equity": str(account.equity),
            "trading_blocked": account.trading_blocked,
            "account_blocked": account.account_blocked,
            "pattern_day_trader": account.pattern_day_trader,
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/paper/positions")
def get_paper_positions(
    credentials: HTTPAuthorizationCredentials = Security(security),
):
    verify_action_key(credentials)

    try:
        positions = get_trading_client().get_all_positions()
        return [
            {
                "symbol": position.symbol,
                "side": str(position.side),
                "qty": str(position.qty),
                "avg_entry_price": str(position.avg_entry_price),
                "market_value": str(position.market_value),
                "cost_basis": str(position.cost_basis),
                "unrealized_pl": str(position.unrealized_pl),
                "unrealized_plpc": str(position.unrealized_plpc),
                "current_price": str(position.current_price),
                "change_today": str(position.change_today),
            }
            for position in positions
        ]
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/paper/orders")
def get_paper_orders(
    credentials: HTTPAuthorizationCredentials = Security(security),
):
    verify_action_key(credentials)

    try:
        orders = get_trading_client().get_orders()
        return [order.model_dump(mode="json") for order in orders]
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/paper/orders")
def submit_paper_order(
    order: NewOrder,
    credentials: HTTPAuthorizationCredentials = Security(security),
):
    verify_action_key(credentials)

    common = {
        "symbol": order.symbol.upper(),
        "side": order_side(order.side),
        "time_in_force": time_in_force(order.time_in_force),
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
        request = LimitOrderRequest(
            **common,
            limit_price=order.limit_price,
        )
    elif order.type == "stop":
        request = StopOrderRequest(
            **common,
            stop_price=order.stop_price,
        )
    else:
        request = StopLimitOrderRequest(
            **common,
            stop_price=order.stop_price,
            limit_price=order.limit_price,
        )

    try:
        result = get_trading_client().submit_order(order_data=request)
        return result.model_dump(mode="json")
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/paper/orders/{order_id}")
def get_paper_order(
    order_id: str,
    credentials: HTTPAuthorizationCredentials = Security(security),
):
    verify_action_key(credentials)

    try:
        order = get_trading_client().get_order_by_id(order_id)
        return order.model_dump(mode="json")
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/paper/orders/{order_id}")
def cancel_paper_order(
    order_id: str,
    credentials: HTTPAuthorizationCredentials = Security(security),
):
    verify_action_key(credentials)

    try:
        get_trading_client().cancel_order_by_id(order_id)
        return {
            "status": "cancellation_requested",
            "order_id": order_id,
        }
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
