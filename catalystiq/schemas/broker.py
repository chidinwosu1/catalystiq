"""Broker-agnostic request/response shapes for paper trading (§1.1 Execution Zone).

`NewOrder` and the response models here are what routers and callers see;
nothing here should leak Alpaca- (or, eventually, Webull-) specific field
names, so swapping the concrete BrokerProvider never changes a caller's
shape.
"""
from __future__ import annotations

import datetime as dt
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class NewOrder(BaseModel):
    symbol: str = Field(min_length=1, max_length=15)
    side: Literal["buy", "sell"]
    type: Literal["market", "limit", "stop", "stop_limit", "trailing_stop"]
    time_in_force: Literal["day", "gtc", "ioc", "fok"] = "day"

    qty: Optional[float] = Field(default=None, gt=0)
    notional: Optional[float] = Field(default=None, gt=0, le=5000)
    limit_price: Optional[float] = Field(default=None, gt=0)
    stop_price: Optional[float] = Field(default=None, gt=0)
    trail_percent: Optional[float] = Field(default=None, gt=0, lt=100)
    trail_price: Optional[float] = Field(default=None, gt=0)
    extended_hours: bool = False
    client_order_id: Optional[str] = Field(default=None, max_length=128)

    # Optional protective exits. Both set = a bracket order (take-profit AND
    # stop-loss legs); one set = a one-triggers-other order (just that leg).
    take_profit_price: Optional[float] = Field(default=None, gt=0)
    stop_loss_price: Optional[float] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_order(self):
        if (self.qty is None) == (self.notional is None):
            raise ValueError("Provide either qty or notional, but not both.")

        if self.type in {"limit", "stop_limit"} and self.limit_price is None:
            raise ValueError("limit_price is required.")

        if self.type in {"stop", "stop_limit"} and self.stop_price is None:
            raise ValueError("stop_price is required.")

        if self.type == "trailing_stop":
            if (self.trail_percent is None) == (self.trail_price is None):
                raise ValueError(
                    "trailing_stop orders need exactly one of trail_percent or trail_price."
                )
        elif self.trail_percent is not None or self.trail_price is not None:
            raise ValueError("trail_percent/trail_price only apply to trailing_stop orders.")

        return self


class AccountInfo(BaseModel):
    status: str
    currency: str
    cash: str
    buying_power: str
    portfolio_value: str
    equity: str
    last_equity: str
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


class ScheduledOrderCreate(BaseModel):
    order: NewOrder
    scheduled_at: dt.datetime

    @model_validator(mode="after")
    def validate_future(self):
        now = dt.datetime.now(self.scheduled_at.tzinfo or dt.timezone.utc)
        if self.scheduled_at <= now:
            raise ValueError("scheduled_at must be in the future.")
        return self


class ScheduledOrderRecord(BaseModel):
    id: int
    symbol: str
    order: NewOrder
    scheduled_at: dt.datetime
    # "due" = the scheduled time has passed and the order is ready for manual
    # review/confirmation; it is NEVER submitted automatically (§13).
    status: Literal["pending", "due", "submitted", "failed", "cancelled"]
    broker_order_id: Optional[str] = None
    error_detail: Optional[str] = None
    created_at: dt.datetime


class OrderReview(BaseModel):
    """The exact details a user must review before confirming an order (§13),
    returned by the confirm endpoint alongside the single-use token."""

    symbol: str
    side: str
    type: str
    time_in_force: str
    qty: Optional[float] = None
    notional: Optional[float] = None
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    estimated_max_loss: Optional[float] = None
    account_id: str
    mode: str


class OrderConfirmationResponse(BaseModel):
    review: OrderReview
    confirmation_token: str
    expires_at: dt.datetime


class ConfirmedOrder(BaseModel):
    """Body for submitting a confirmed order (§13): the order, the account it
    was confirmed against, and the single-use token."""

    order: NewOrder
    account_id: str
    confirmation_token: str
