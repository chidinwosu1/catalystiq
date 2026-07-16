"""Broker-agnostic request/response shapes for paper trading (§1.1 Execution Zone).

`NewOrder` and the response models here are what routers and callers see;
nothing here should leak Alpaca- (or, eventually, Webull-) specific field
names, so swapping the concrete BrokerProvider never changes a caller's
shape.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


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


class AccountInfo(BaseModel):
    status: str
    currency: str
    cash: str
    buying_power: str
    portfolio_value: str
    equity: str
    trading_blocked: bool
    account_blocked: bool
    pattern_day_trader: bool


class Position(BaseModel):
    symbol: str
    side: str
    qty: str
    avg_entry_price: str
    market_value: str
    cost_basis: str
    unrealized_pl: str
    unrealized_plpc: str
    current_price: str
    change_today: str
