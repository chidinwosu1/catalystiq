"""A market-data provider that fails over to a secondary source ONLY when the
primary is rate-limited.

Motivation: the daily universe scan (Setup Strength) fetches OHLCV history for
~24 symbols through Yahoo, which per-IP-throttles the shared Render egress
(documented in NVDA_RATE_LIMIT_DIAGNOSIS.md). When that happens every symbol is
skipped and the Trade Center shows "warming up" with no cards. This wrapper
keeps Yahoo as primary but, on a rate-limit (429) failure of an OHLCV or quote
call, transparently retries the same call against a secondary provider (Webull
OpenAPI Market Data). Any other error (bad symbol, empty history) is NOT
masked - only throttling triggers failover.

Scope: only price/quote calls fail over. ``get_fundamentals`` / ``get_news`` go
to the primary alone, because the secondary (Webull market data) does not
provide them - so wrapping the global provider never changes fundamentals/news
behavior. Opt-in via ``market_data_fallback_provider``; off by default.
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


class FallbackMarketDataProvider(MarketDataProvider):
    """Delegate to ``primary``; on a RATE-LIMIT failure of a price/quote call,
    retry once against ``secondary``. Non-throttle errors propagate unchanged."""

    def __init__(self, primary: MarketDataProvider, secondary: MarketDataProvider) -> None:
        self._primary = primary
        self._secondary = secondary
        pname = getattr(primary, "PROVIDER_NAME", type(primary).__name__)
        sname = getattr(secondary, "PROVIDER_NAME", type(secondary).__name__)
        # A distinct gate key so failover traffic isn't attributed to the raw
        # primary's circuit breaker (whose 429s we are deliberately absorbing).
        self.PROVIDER_NAME = f"{pname}+{sname}_fallback"

    def _with_failover(self, method: str, *args, **kwargs):
        primary_fn = getattr(self._primary, method, None)
        if not callable(primary_fn):
            raise AttributeError(f"primary provider has no {method!r}")
        try:
            return primary_fn(*args, **kwargs)
        except Exception as exc:
            if not is_rate_limited_error(exc):
                raise  # only throttling triggers failover
            secondary_fn = getattr(self._secondary, method, None)
            if not callable(secondary_fn):
                raise
            try:
                return secondary_fn(*args, **kwargs)
            except MarketDataError:
                raise  # already the pipeline's expected type
            except Exception as sec_exc:
                # Normalize a secondary-specific failure (e.g. Twelve Data raises
                # its own ProviderError, which is a *sibling* of MarketDataError,
                # not a subclass) into MarketDataError. Otherwise it escapes the
                # daily pipeline / universe scan's `except MarketDataError` and
                # crashes the whole scan instead of just skipping the symbol.
                raise MarketDataError(
                    f"fallback secondary {method} failed: {sec_exc}"
                ) from sec_exc

    # -- price / quote: fail over on throttle --------------------------------

    def get_quote(self, symbol: str) -> Quote:
        return self._with_failover("get_quote", symbol)

    def get_ohlcv(
        self, symbol: str, start: dt.date, end: dt.date | None = None, interval: str = "1d"
    ) -> list[OHLCVBar]:
        return self._with_failover("get_ohlcv", symbol, start, end, interval)

    def get_intraday_ohlcv(
        self, symbol: str, *, interval: str = "5m", days: int = 20
    ) -> list[IntradayBar]:
        return self._with_failover("get_intraday_ohlcv", symbol, interval=interval, days=days)

    # -- primary-only (secondary doesn't provide these) ----------------------

    def get_fundamentals(self, symbol: str) -> FundamentalsSnapshot:
        return self._primary.get_fundamentals(symbol)

    def get_news(self, symbol: str, limit: int = 10) -> list[NewsItem]:
        return self._primary.get_news(symbol, limit=limit)


class ChainedMarketDataProvider(MarketDataProvider):
    """An ORDERED price-data chain: try each provider in turn and, on ANY
    failure, fail over to the next one. Used by the opportunity-scan / warmer
    price path (e.g. Webull primary -> Twelve Data fallback).

    Difference from :class:`FallbackMarketDataProvider`: that wrapper keeps a
    single primary and only fails over on an upstream *rate limit* (it exists to
    absorb Yahoo 429s while keeping Yahoo authoritative). This chain is a
    deliberate source-priority ordering, so it fails over on *any* error from a
    provider - a rate limit, a missing entitlement, an empty history, an SDK
    binding mismatch - because the whole point is that the fallback should cover
    for the primary however it fails.

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
