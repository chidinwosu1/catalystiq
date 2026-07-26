"""Company news from Finnhub, shaped as NewsItem - the Yahoo-news replacement.

Finnhub's ``/company-news`` endpoint returns recent articles for a symbol over a
date window. This adapter fetches through the shared HttpTransport (retries +
circuit breaker) behind a conservative token-bucket rate limiter (Finnhub free
tier is ~60 req/min), maps each article to the app's NewsItem, and serves
results from a short-TTL per-symbol cache so repeated/bursty requests don't spend
the rate budget. It NEVER fabricates an article - a fetch failure raises
MarketDataError and an empty window returns [].
"""
from __future__ import annotations

import datetime as dt
import threading
import time as _time

from catalystiq.providers.base import DataDomain, ProviderError, ProviderErrorCategory
from catalystiq.providers.fetch_tracker import record_fetch
from catalystiq.providers.market_data import MarketDataError
from catalystiq.providers.transport import HttpTransport, RateLimiter
from catalystiq.schemas.market_data import NewsItem

_BASE = "https://finnhub.io/api/v1"


class _CacheEntry:
    __slots__ = ("items", "stored_at")

    def __init__(self, items: list[NewsItem], stored_at: float):
        self.items = items
        self.stored_at = stored_at


class FinnhubNewsProvider:
    """Serves get_news(symbol, limit) -> list[NewsItem] from Finnhub company
    news. ``api_key`` is required. ``transport`` and ``monotonic`` are injectable
    for offline tests."""

    PROVIDER_NAME = "finnhub"
    DOMAIN = DataDomain.NEWS
    ADAPTER_VERSION = "1.0.0"

    def __init__(
        self,
        api_key: str,
        transport: HttpTransport | None = None,
        *,
        lookback_days: int = 14,
        cache_ttl_seconds: float = 600.0,
        monotonic=_time.monotonic,
    ) -> None:
        if not api_key:
            raise ProviderError(
                "FINNHUB_API_KEY is not configured.",
                category=ProviderErrorCategory.CONFIG,
                provider=self.PROVIDER_NAME,
            )
        self._api_key = api_key
        self._lookback_days = max(1, lookback_days)
        self._cache_ttl = cache_ttl_seconds
        self._monotonic = monotonic
        # Free tier ~60 req/min = 1/s; allow a small burst then pace.
        self._transport = transport or HttpTransport(
            self.PROVIDER_NAME, base_url=_BASE,
            rate_limiter=RateLimiter(rate_per_sec=1.0, capacity=30),
        )
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()

    def _get(self, path: str, params: dict):
        params = {**params, "token": self._api_key}
        try:
            return self._transport.request("GET", path, params=params).raise_for_status().json()
        except ProviderError as exc:
            # Normalize transport/HTTP failures (401 bad key, 429 throttle, 5xx)
            # into MarketDataError so the news route handles them uniformly. The
            # message keeps the status so rate limits stay detectable.
            raise MarketDataError(f"Finnhub news request failed: {exc}") from exc

    def get_news(self, symbol: str, limit: int = 10) -> list[NewsItem]:
        symbol = symbol.upper()
        limit = max(1, limit)
        cache_key = symbol
        # Cache the full fetched window per symbol; slice to `limit` on read so
        # different limits share one cached fetch.
        with self._lock:
            entry = self._cache.get(cache_key)
            if entry is not None and (self._monotonic() - entry.stored_at) < self._cache_ttl:
                return entry.items[:limit]

        today = dt.date.today()
        start = today - dt.timedelta(days=self._lookback_days)
        data = self._get("company-news", {
            "symbol": symbol,
            "from": start.isoformat(),
            "to": today.isoformat(),
        })
        record_fetch(self.PROVIDER_NAME)

        items: list[NewsItem] = []
        for raw in data if isinstance(data, list) else []:
            item = _parse_article(raw)
            if item is not None:
                items.append(item)
        # Most recent first.
        items.sort(key=lambda n: n.published_at, reverse=True)

        with self._lock:
            self._cache[cache_key] = _CacheEntry(items=items, stored_at=self._monotonic())
        return items[:limit]


def _parse_article(raw: dict) -> NewsItem | None:
    if not isinstance(raw, dict):
        return None
    headline = (raw.get("headline") or "").strip()
    url = (raw.get("url") or "").strip()
    if not headline or not url:
        return None  # skip incomplete rows rather than fabricate
    ts = raw.get("datetime")
    try:
        published_at = (
            dt.datetime.fromtimestamp(int(ts), dt.timezone.utc)
            if ts else dt.datetime.now(dt.timezone.utc)
        )
    except (TypeError, ValueError, OSError):
        published_at = dt.datetime.now(dt.timezone.utc)
    summary = (raw.get("summary") or "").strip() or None
    return NewsItem(
        headline=headline,
        source_url=url,
        published_at=published_at,
        category=raw.get("source") or raw.get("category"),
        summary=summary,
    )


def get_finnhub_news_provider() -> FinnhubNewsProvider:
    """Build the Finnhub news adapter from settings. Raises MarketDataError when
    FINNHUB_API_KEY is unset, so the news route surfaces it as a 502."""
    from catalystiq.config import get_settings

    settings = get_settings()
    try:
        return FinnhubNewsProvider(
            settings.finnhub_api_key,
            lookback_days=settings.finnhub_news_lookback_days,
            cache_ttl_seconds=settings.finnhub_news_cache_ttl_seconds,
        )
    except ProviderError as exc:
        raise MarketDataError(str(exc)) from exc
