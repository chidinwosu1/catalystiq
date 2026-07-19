"""Twelve Data provider (§5): an OPTIONAL, restricted personal-use secondary
market-data source. See TWELVE_DATA_COMPLIANCE.md.

Disabled by default (no key => not constructed). Used only for private,
personal, non-commercial cross-provider validation. It NEVER silently replaces
Yahoo's values, and its raw values are NOT persisted: the comparison service
(catalystiq/pipelines/comparison.py) records only the tolerance outcome and
provenance for a restricted provider, never the raw value or a reconstructable
difference.

Plan-limit compliance: every request routes through the process-central credit
gate (twelve_data_gate.py), which enforces the Basic plan's 8 credits/min and
800 credits/day with per-endpoint credit weights. The provider auto-shuts-off
(fails closed) when the daily cap is hit or when credential/licensing
validation fails, and stays optional so the app works with it disabled.
"""
from __future__ import annotations

import datetime as dt

from catalystiq.providers.base import DataDomain, ProviderErrorCategory
from catalystiq.providers.market_data import MarketDataError, MarketDataProvider
from catalystiq.providers.transport import HttpTransport, ProviderError, RateLimiter
from catalystiq.providers.twelve_data_gate import TwelveDataGate, get_twelve_data_gate
from catalystiq.schemas.market_data import (
    ExchangeInfo,
    OHLCVBar,
    Quote,
    SymbolSearchResult,
)

_BASE = "https://api.twelvedata.com"

# Substrings in a Twelve Data error body that mean the KEY is bad (credential
# validation failed) -> auto-disable.
_CREDENTIAL_HINTS = ("api key", "apikey", "invalid api", "authentication", "unauthorized")
# Substrings that mean the DATA/endpoint needs a higher plan or extra licensing
# (professional/exchange/redistribution) -> auto-disable (fail closed).
_LICENSING_HINTS = (
    "upgrade your plan",
    "not available on your plan",
    "requires a paid",
    "professional",
    "redistribut",
    "license",
    "permission denied",
    "not authorized for this plan",
)

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
    ADAPTER_VERSION = "2.0.0"
    DOMAIN = DataDomain.MARKET_DATA
    # Comparison/persistence code must NOT store this provider's raw values or
    # any reconstructable derived value (retention not yet confirmed).
    RESTRICTED_NO_RAW_PERSIST = True

    def __init__(
        self,
        api_key: str,
        transport: HttpTransport | None = None,
        gate: TwelveDataGate | None = None,
    ) -> None:
        if not api_key:
            raise ProviderError(
                "Twelve Data api_key is not configured.",
                category=ProviderErrorCategory.CONFIG,
                provider=self.PROVIDER_NAME,
            )
        self._api_key = api_key
        # Central credit gate (8/min, 800/day, weighted) shared across the app.
        self._gate = gate or get_twelve_data_gate()
        # Free tier: ~8 requests/minute (secondary pacing; the gate is authoritative).
        self._transport = transport or HttpTransport(
            self.PROVIDER_NAME, base_url=_BASE,
            rate_limiter=RateLimiter(rate_per_sec=8 / 60, capacity=8),
        )

    def _get(self, path: str, params: dict) -> dict:
        # Central credit enforcement BEFORE the request (fails closed if a limit
        # is hit or the provider is auto-disabled).
        self._gate.charge_endpoint(path)
        params = {**params, "apikey": self._api_key}
        try:
            resp = self._transport.request("GET", path, params=params).raise_for_status()
        except ProviderError as exc:
            # A hard auth failure means the credential is invalid -> shut off.
            if exc.category is ProviderErrorCategory.AUTH:
                self._gate.disable(f"credential validation failed (HTTP {exc.status_code})")
            raise
        data = resp.json()
        # Twelve Data reports request errors in a 200 body. Inspect the message
        # to auto-disable on credential or licensing/plan failures.
        if isinstance(data, dict) and data.get("status") == "error":
            message = str(data.get("message") or "")
            low = message.lower()
            if any(h in low for h in _CREDENTIAL_HINTS):
                self._gate.disable("credential validation failed")
            elif any(h in low for h in _LICENSING_HINTS):
                self._gate.disable("licensing/plan restriction")
            raise ProviderError(
                f"Twelve Data error: {message}",
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

    # The central gate (built from settings) enforces the plan credit limits.
    return TwelveDataProvider(get_settings().twelve_data_api_key)
