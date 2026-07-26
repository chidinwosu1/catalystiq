"""MarketDataProvider interface (§1.1) and the Yahoo Finance implementation.

Every module in the analytical engine (§2.2) reads market/fundamentals/news
data through this interface rather than talking to Yahoo Finance directly,
so the concrete source can be swapped later without touching module code.
"""
from __future__ import annotations

import datetime as dt
import logging
from abc import ABC, abstractmethod

from catalystiq.providers.base import DataDomain
from catalystiq.providers.fetch_tracker import record_fetch
from catalystiq.schemas.market_data import (
    FundamentalsSnapshot,
    IntradayBar,
    NewsItem,
    OHLCVBar,
    Quote,
)


class MarketDataProvider(ABC):
    """Abstract source of quotes, historical OHLCV, fundamentals, and news."""

    @abstractmethod
    def get_quote(self, symbol: str) -> Quote:
        """Latest/live price for `symbol`."""

    @abstractmethod
    def get_ohlcv(
        self,
        symbol: str,
        start: dt.date,
        end: dt.date | None = None,
        interval: str = "1d",
    ) -> list[OHLCVBar]:
        """Historical OHLCV bars for `symbol` between `start` and `end` (inclusive)."""

    @abstractmethod
    def get_fundamentals(self, symbol: str) -> FundamentalsSnapshot:
        """Latest fundamentals snapshot for `symbol`."""

    @abstractmethod
    def get_news(self, symbol: str, limit: int = 10) -> list[NewsItem]:
        """Recent news items for `symbol`, most recent first."""


_logger = logging.getLogger(__name__)


class MarketDataError(RuntimeError):
    """Raised when a provider fails to fetch or parse data."""


def get_market_data_provider() -> MarketDataProvider:
    """The general market-data (OHLCV/quote) provider: the SAME ordered price
    chain the opportunity scan uses (``MARKET_DATA_PRIMARY_PROVIDER`` ->
    ``MARKET_DATA_FALLBACK_PROVIDER``, e.g. Webull -> Twelve Data).

    Yahoo has been fully removed. Company fundamentals are served by SEC EDGAR
    and company news by Finnhub through their OWN providers (see
    catalystiq/providers/sec_fundamentals.py and finnhub_news.py), NOT this
    factory - the price chain raises for get_fundamentals/get_news."""
    return get_scan_market_data_provider()


# --- Opportunity-scan price chain -------------------------------------------
# The Trade Center scan and its background warmer fetch OHLCV/quotes through a
# DEDICATED, ordered chain (primary -> fallback), independent of the global
# get_market_data_provider() above (which still serves fundamentals/news). This
# keeps Yahoo out of the scan when configured for Webull -> Twelve Data, while
# never repointing fundamentals/news to a provider that can't serve them.


class _UnavailableMarketDataProvider(MarketDataProvider):
    """A price provider that fails every fetch with a MarketDataError, used when
    no configured scan provider can be built (missing credentials / API key).
    Its failures are indistinguishable to the scan from a data outage, so the
    Trade Center reports an honest "unavailable" status and keeps retrying -
    rather than crashing or silently falling back to Yahoo."""

    PROVIDER_NAME = "unavailable"

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def get_quote(self, symbol: str) -> Quote:
        raise MarketDataError(self._reason)

    def get_ohlcv(
        self, symbol: str, start: dt.date, end: dt.date | None = None, interval: str = "1d"
    ) -> list[OHLCVBar]:
        raise MarketDataError(self._reason)

    def get_intraday_ohlcv(self, symbol: str, *, interval: str = "5m", days: int = 20):
        raise MarketDataError(self._reason)

    def get_fundamentals(self, symbol: str) -> FundamentalsSnapshot:
        raise MarketDataError(self._reason)

    def get_news(self, symbol: str, limit: int = 10) -> list[NewsItem]:
        raise MarketDataError(self._reason)


def _build_named_market_data_provider(name: str) -> MarketDataProvider | None:
    """Construct a price provider by config name, or return None for an empty
    name. Raises on an unknown name; a missing-credential failure propagates so
    the caller can decide to skip that leg of the chain.

      "webull"      - Webull OpenAPI Market Data (daily d1 bars + quotes).
      "twelve_data" - Twelve Data (daily OHLCV + quote) via TWELVE_DATA_API_KEY.
    """
    key = (name or "").strip().lower()
    if not key:
        return None
    if key == "webull":
        return get_webull_market_data_provider()
    if key == "twelve_data":
        from catalystiq.providers.twelve_data import get_twelve_data_provider

        return get_twelve_data_provider()
    raise ValueError(f"Unknown price provider {name!r}")


def get_scan_market_data_provider() -> MarketDataProvider:
    """The ordered price chain the opportunity scan + warmer use: the configured
    primary (``market_data_primary_provider``) then the fallback
    (``market_data_fallback_provider``). A leg that can't be built (missing
    creds/key) is skipped; a single surviving leg is returned bare; two or more
    become a :class:`ChainedMarketDataProvider` (fail over on any error). When
    nothing can be built, an :class:`_UnavailableMarketDataProvider` is returned
    so the scan degrades to an honest "unavailable" instead of crashing."""
    from catalystiq.config import get_settings

    settings = get_settings()
    order = [
        settings.market_data_primary_provider,
        settings.market_data_fallback_provider,
    ]

    built: list[MarketDataProvider] = []
    seen: set[str] = set()
    attempted: list[str] = []
    for raw in order:
        key = (raw or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        attempted.append(key)
        try:
            provider = _build_named_market_data_provider(key)
        except Exception as exc:  # missing creds / unknown name -> skip this leg
            _logger.warning("scan price provider %r could not be built: %s", key, exc)
            continue
        if provider is not None:
            built.append(provider)

    if not built:
        names = ", ".join(attempted) or "(none configured)"
        return _UnavailableMarketDataProvider(
            f"No scan price provider could be built from [{names}] - check "
            "MARKET_DATA_PRIMARY_PROVIDER/MARKET_DATA_FALLBACK_PROVIDER credentials."
        )
    if len(built) == 1:
        return built[0]

    from catalystiq.providers.fallback_market_data import ChainedMarketDataProvider

    return ChainedMarketDataProvider(built)


# --- Dedicated intraday (Entry Check) provider ------------------------------
# The real-time Entry Quality / Entry Check feed uses its OWN provider so the
# daily pipeline, fundamentals and news stay on Yahoo. Webull's OpenAPI Market
# Data serves real-time L1 US quotes + 1m/5m bars; Yahoo (default) reuses the
# daily provider. The Webull client is expensive to build (signed SDK client),
# so instances are cached per credential set; construction failures are NOT
# cached (a missing-credential error surfaces on every request).

import threading as _threading  # noqa: E402

_intraday_provider_cache: dict[tuple, MarketDataProvider] = {}
_intraday_provider_lock = _threading.Lock()


def reset_intraday_provider_cache() -> None:
    """Drop any cached intraday provider. Test-support / config-reload hook."""
    with _intraday_provider_lock:
        _intraday_provider_cache.clear()


def get_webull_market_data_provider() -> MarketDataProvider:
    """The cached Webull OpenAPI Market Data provider (real-time L1 US quotes +
    1m/5m bars). Cached per credential set - construction is an expensive signed
    SDK client build. Raises MarketDataError when credentials are missing or the
    client can't be built (callers decide whether to degrade)."""
    from catalystiq.config import get_settings

    settings = get_settings()
    key = (
        "webull",
        settings.webull_app_key,
        settings.webull_app_secret,
        settings.webull_region_id,
        settings.webull_mdata_api_base_url,
    )
    cached = _intraday_provider_cache.get(key)
    if cached is not None:
        return cached
    with _intraday_provider_lock:
        cached = _intraday_provider_cache.get(key)
        if cached is not None:
            return cached
        from catalystiq.providers.webull_market_data import WebullMarketDataProvider

        provider = WebullMarketDataProvider(
            settings.webull_app_key,
            settings.webull_app_secret,
            region_id=settings.webull_region_id,
            api_endpoint=settings.webull_mdata_api_base_url,
        )
        _intraday_provider_cache[key] = provider
        return provider


def get_intraday_market_data_provider() -> MarketDataProvider:
    """The provider that serves the real-time Entry Check feed, chosen by
    ``intraday_market_data_provider``. Defaults to (and falls back to) the Yahoo
    daily provider; ``"webull"`` uses Webull OpenAPI Market Data via the existing
    Webull app credentials. Never raises for an unknown value - it degrades to
    the default provider, so Entry Check keeps working (delayed) rather than
    500-ing."""
    from catalystiq.config import get_settings

    choice = (get_settings().intraday_market_data_provider or "yahoo").strip().lower()
    if choice == "webull":
        return get_webull_market_data_provider()
    # Default / "yahoo" / any unknown value: reuse the daily provider.
    return get_market_data_provider()
