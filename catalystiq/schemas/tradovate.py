"""Pydantic shapes returned by the Tradovate futures adapter
(catalystiq/providers/tradovate.py).

Provider-agnostic on purpose (mirroring schemas/market_data.py): nothing here
leaks a Tradovate-specific transport field, so a caller that reads a futures
contract/product never depends on the raw REST payload's key names. These
describe *reference* futures data (the contract & product definitions the
Tradovate REST API serves) - real-time quotes and historical bars are delivered
over Tradovate's Market Data WebSocket and are intentionally out of scope for
this REST adapter (see the provider docstring)."""
from __future__ import annotations

from pydantic import BaseModel


class FuturesContract(BaseModel):
    """One tradable futures contract (a specific expiry of a product), e.g.
    ``ESM5``. ``tick_size`` is the provider's minimum price increment when the
    API reports it."""

    id: int
    name: str
    product_id: int | None = None
    contract_maturity_id: int | None = None
    status: str | None = None
    tick_size: float | None = None


class FuturesProduct(BaseModel):
    """A futures product definition (the root instrument, e.g. ``ES`` = E-mini
    S&P 500), independent of any single expiry. ``value_per_point`` and
    ``tick_size`` describe contract economics when the API reports them."""

    id: int
    name: str
    product_type: str | None = None
    description: str | None = None
    exchange_id: int | None = None
    currency_id: int | None = None
    value_per_point: float | None = None
    tick_size: float | None = None
    price_format_type: str | None = None
    status: str | None = None
