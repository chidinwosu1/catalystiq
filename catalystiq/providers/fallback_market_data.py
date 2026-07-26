"""Ordered price-data provider chaining for the Webull -> Twelve Data path.

The daily universe scan (Setup Strength), its background warmer, and the general
price path all fetch OHLCV/quotes through this chain. It tries the configured
primary (Webull) first and, on ANY failure, fails over to the fallback (Twelve
Data); when every leg fails it raises a single MarketDataError so the scan
reports an honest "unavailable" status. Only price/quote/intraday calls are
served - company fundamentals (SEC EDGAR) and news (Finnhub) route through their
own providers, never this chain.
"""
from __future__ import annotations

import datetime as dt

from catalystiq.providers.fundamentals_cache import is_rate_limited_error
from catalystiq.providers.market_data import MarketDataError, MarketDataProvider
from catalystiq.schemas.market_data import (
    FundamentalsSnapshot,
    IntradayBar,
    NewsItem,
    OHLCVBar,
    Quote,
)


class ChainedMarketDataProvider(MarketDataProvider):
    """An ORDERED price-data chain: try each provider in turn and, on ANY
    failure, fail over to the next one. Used by the opportunity-scan / warmer /
    general price path (Webull primary -> Twelve Data fallback).

    It fails over on *any* error from a provider - a rate limit, a missing
    entitlement, an empty history, an SDK binding mismatch - because the whole
    point of a source-priority ordering is that the fallback should cover for
    the primary however it fails.

    Only price/quote/intraday calls are served (the scan never asks a price
    provider for fundamentals/news; those go to SEC EDGAR / Finnhub on their own
    providers). When every provider fails, the aggregated error is raised as a
    single :class:`MarketDataError` whose message preserves the underlying
    detail AND stays detectable as a rate limit when any leg was rate-limited,
    so the scan reports the correct honest "unavailable" reason. Never fabricates
    data."""

    def __init__(self, providers: list[MarketDataProvider]) -> None:
        chain = [p for p in providers if p is not None]
        if not chain:
            raise ValueError("ChainedMarketDataProvider needs at least one provider")
        self._providers = chain
        names = [getattr(p, "PROVIDER_NAME", type(p).__name__) for p in chain]
        # A distinct gate key so the chain's ingest traffic has its own
        # concurrency + circuit-breaker state, separate from each leg's.
        self.PROVIDER_NAME = ">".join(names) + "_chain"

    def _try_in_order(self, method: str, *args, **kwargs):
        errors: list[tuple[str, BaseException]] = []
        rate_limited_any = False
        for provider in self._providers:
            fn = getattr(provider, method, None)
            if not callable(fn):
                continue
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - try the next provider
                rate_limited_any = rate_limited_any or is_rate_limited_error(exc)
                name = getattr(provider, "PROVIDER_NAME", type(provider).__name__)
                errors.append((name, exc))
        if not errors:
            raise MarketDataError(f"no configured provider supports {method!r}.")
        detail = "; ".join(f"{name}: {exc}" for name, exc in errors)
        message = f"all price providers failed for {method}: {detail}"
        # Keep the aggregated error detectable as a rate limit when any leg was
        # throttled, so scan_universe reports the rate-limit note (and the gate's
        # circuit-breaker trips) rather than a generic outage.
        if rate_limited_any and not is_rate_limited_error(MarketDataError(message)):
            message = "rate limit: " + message
        raise MarketDataError(message) from errors[-1][1]

    def get_quote(self, symbol: str) -> Quote:
        return self._try_in_order("get_quote", symbol)

    def get_ohlcv(
        self, symbol: str, start: dt.date, end: dt.date | None = None, interval: str = "1d"
    ) -> list[OHLCVBar]:
        return self._try_in_order("get_ohlcv", symbol, start, end, interval)

    def get_intraday_ohlcv(
        self, symbol: str, *, interval: str = "5m", days: int = 20
    ) -> list[IntradayBar]:
        return self._try_in_order("get_intraday_ohlcv", symbol, interval=interval, days=days)

    # A price chain never serves fundamentals/news - those route to SEC EDGAR /
    # Finnhub on their own providers. Raise honestly rather than returning empty.
    def get_fundamentals(self, symbol: str) -> FundamentalsSnapshot:
        raise MarketDataError("The scan price chain does not provide fundamentals.")

    def get_news(self, symbol: str, limit: int = 10) -> list[NewsItem]:
        raise MarketDataError("The scan price chain does not provide news.")
