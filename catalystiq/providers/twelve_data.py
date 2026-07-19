"""Twelve Data provider (§5): an OPTIONAL secondary market-data source.

Disabled by default (no key => not constructed). Used for cross-provider
validation samples, development comparison, and - only when explicitly
configured - as a Yahoo outage fallback. It NEVER silently replaces Yahoo's
values: the comparison service (catalystiq/pipelines/comparison.py) records
both providers' values and their difference rather than averaging or
overwriting.

Free-tier aware: a token-bucket rate limiter paces requests to the free
plan's ~8/min, and an optional per-process request budget refuses to consume
the whole daily allowance in one run (raises RATE_LIMITED past the budget).
The budget is in-memory per adapter instance - a best-effort guard, not a
persistent daily counter (documented limitation).
"""
from __future__ import annotations

import datetime as dt

from catalystiq.providers.base import DataDomain, ProviderErrorCategory
from catalystiq.providers.market_data import MarketDataError, MarketDataProvider
from catalystiq.providers.transport import HttpTransport, ProviderError, RateLimiter
from catalystiq.schemas.market_data import (
    ExchangeInfo,
    OHLCVBar,
    Quote,
    SymbolSearchResult,
)

_BASE = "https://api.twelvedata.com"

_INTERVAL_MAP = {
    "1d": "1day",
    "1day": "1day",
    "1wk": "1week",
    "1week": "1week",
    "1mo": "1month",
    "1month": "1month",
    "1h": "1h",
    "1min": "1min",
    "5min": "5min",
    "15min": "15min",
    "30min": "30min",
}


class TwelveDataProvider(MarketDataProvider):
    PROVIDER_NAME = "twelve_data"
    ADAPTER_VERSION = "1.0.0"
    DOMAIN = DataDomain.MARKET_DATA

    def __init__(
        self,
        api_key: str,
        transport: HttpTransport | None = None,
        request_budget: int = 0,
    ) -> None:
        if not api_key:
            raise ProviderError(
                "Twelve Data api_key is not configured.",
                category=ProviderErrorCategory.CONFIG,
                provider=self.PROVIDER_NAME,
            )
        self._api_key = api_key
        self._budget = request_budget  # 0 => unlimited
        self._used = 0
        # Free tier: ~8 requests/minute.
        self._transport = transport or HttpTransport(
            self.PROVIDER_NAME, base_url=_BASE,
            rate_limiter=RateLimiter(rate_per_sec=8 / 60, capacity=8),
        )

    def _spend(self) -> None:
        if self._budget and self._used >= self._budget:
            raise ProviderError(
                f"Twelve Data per-run request budget ({self._budget}) exhausted - "
                "not consuming more of the daily allowance.",
                category=ProviderErrorCategory.RATE_LIMITED,
                provider=self.PROVIDER_NAME,
            )
        self._used += 1

    def _get(self, path: str, params: dict) -> dict:
        self._spend()
        params = {**params, "apikey": self._api_key}
        data = self._transport.request("GET", path, params=params).raise_for_status().json()
        # Twelve Data reports request errors in a 200 body.
        if isinstance(data, dict) and data.get("status") == "error":
            raise ProviderError(
                f"Twelve Data error: {data.get('message')}",
                category=ProviderErrorCategory.UNAVAILABLE,
                provider=self.PROVIDER_NAME,
            )
        return data

    def get_quote(self, symbol: str) -> Quote:
        data = self._get("quote", {"symbol": symbol.upper()})
        price = _as_float(data.get("close"))
        if price is None:
            raise MarketDataError(f"No Twelve Data quote for {symbol}")
        ts = data.get("timestamp")
        as_of = (
            dt.datetime.fromtimestamp(int(ts), dt.timezone.utc)
            if ts
            else dt.datetime.now(dt.timezone.utc)
        )
        return Quote(
            symbol=symbol.upper(),
            price=price,
            previous_close=_as_float(data.get("previous_close")),
            as_of=as_of,
        )

    def get_ohlcv(
        self,
        symbol: str,
        start: dt.date,
        end: dt.date | None = None,
        interval: str = "1d",
    ) -> list[OHLCVBar]:
        td_interval = _INTERVAL_MAP.get(interval)
        if td_interval is None:
            raise MarketDataError(f"Unsupported Twelve Data interval {interval!r}")
        end = end or dt.date.today()
        params = {
            "symbol": symbol.upper(),
            "interval": td_interval,
            "start_date": start.isoformat(),
            "end_date": (end + dt.timedelta(days=1)).isoformat(),
            "order": "ASC",
            "outputsize": 5000,
        }
        data = self._get("time_series", params)
        bars: list[OHLCVBar] = []
        for row in data.get("values", []) or []:
            bar_date = _parse_date(row.get("datetime"))
            if bar_date is None:
                continue
            try:
                bars.append(
                    OHLCVBar(
                        date=bar_date,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=int(float(row.get("volume") or 0)),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        bars.sort(key=lambda b: b.date)
        return bars

    def symbol_search(self, query: str) -> list[SymbolSearchResult]:
        data = self._get("symbol_search", {"symbol": query})
        return [
            SymbolSearchResult(
                symbol=row.get("symbol", ""),
                instrument_name=row.get("instrument_name"),
                exchange=row.get("exchange"),
                instrument_type=row.get("instrument_type"),
                country=row.get("country"),
                currency=row.get("currency"),
            )
            for row in data.get("data", []) or []
            if row.get("symbol")
        ]

    def get_exchanges(self) -> list[ExchangeInfo]:
        data = self._get("exchanges", {})
        return [
            ExchangeInfo(
                name=row.get("name", ""),
                code=row.get("code"),
                country=row.get("country"),
                timezone=row.get("timezone"),
            )
            for row in data.get("data", []) or []
            if row.get("name")
        ]

    # Twelve Data is a market-data source only; fundamentals/news aren't part
    # of this integration. Raise rather than silently returning empty data.
    def get_fundamentals(self, symbol: str):
        raise MarketDataError("Twelve Data adapter does not provide fundamentals.")

    def get_news(self, symbol: str, limit: int = 10):
        raise MarketDataError("Twelve Data adapter does not provide news.")


def _as_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def get_twelve_data_provider() -> TwelveDataProvider:
    from catalystiq.config import get_settings

    settings = get_settings()
    return TwelveDataProvider(
        settings.twelve_data_api_key,
        request_budget=settings.twelve_data_daily_request_budget,
    )
