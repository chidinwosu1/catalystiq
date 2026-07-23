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
from catalystiq.providers.market_data import MarketDataProvider
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
            # Let the secondary's own error (incl. its rate limit) propagate.
            return secondary_fn(*args, **kwargs)

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
