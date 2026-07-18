"""MarketDataProvider interface (§1.1) and the Yahoo Finance implementation.

Every module in the analytical engine (§2.2) reads market/fundamentals/news
data through this interface rather than talking to Yahoo Finance directly,
so the concrete source can be swapped later without touching module code.
"""
from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod

from catalystiq.providers.base import DataDomain
from catalystiq.schemas.market_data import (
    FundamentalsSnapshot,
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


class MarketDataError(RuntimeError):
    """Raised when a provider fails to fetch or parse data."""


class YahooFinanceProvider(MarketDataProvider):
    """MarketDataProvider backed by Yahoo Finance via the `yfinance` package."""

    # Provider identity, per the ProviderAdapter contract
    # (catalystiq/providers/base.py). PROVIDER_NAME is the stable registry
    # key and what a Bronze run's `provider` field records going forward.
    PROVIDER_NAME = "yahoo"
    DOMAIN = DataDomain.MARKET_DATA

    # Bumped whenever this adapter's parsing/field-mapping logic changes -
    # persisted on every Bronze ingestion run (catalystiq/pipelines/
    # market_price_pipeline.py) so a Gold result can be traced back to
    # exactly which version of this adapter produced its source data.
    ADAPTER_VERSION = "1.0.0"

    def __init__(self) -> None:
        # Imported lazily so importing this module doesn't require yfinance
        # (and its heavy transitive deps) unless this provider is actually used.
        import yfinance as yf

        self._yf = yf

    def _ticker(self, symbol: str):
        return self._yf.Ticker(symbol)

    def get_quote(self, symbol: str) -> Quote:
        ticker = self._ticker(symbol)
        try:
            fast = ticker.fast_info
            price = fast["last_price"]
            previous_close = fast.get("previous_close") if hasattr(fast, "get") else None
        except Exception as exc:  # pragma: no cover - network/library errors
            raise MarketDataError(f"Failed to fetch quote for {symbol}: {exc}") from exc

        if price is None:
            raise MarketDataError(f"No quote available for {symbol}")

        return Quote(
            symbol=symbol.upper(),
            price=float(price),
            previous_close=float(previous_close) if previous_close is not None else None,
            as_of=dt.datetime.now(dt.timezone.utc),
        )

    def get_ohlcv(
        self,
        symbol: str,
        start: dt.date,
        end: dt.date | None = None,
        interval: str = "1d",
    ) -> list[OHLCVBar]:
        end = end or dt.date.today()
        try:
            df = self._ticker(symbol).history(
                start=start.isoformat(),
                end=(end + dt.timedelta(days=1)).isoformat(),
                interval=interval,
                auto_adjust=False,
            )
        except Exception as exc:  # pragma: no cover - network/library errors
            raise MarketDataError(f"Failed to fetch OHLCV for {symbol}: {exc}") from exc

        if df.empty:
            return []

        bars: list[OHLCVBar] = []
        for index, row in df.iterrows():
            bars.append(
                OHLCVBar(
                    date=index.date(),
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=int(row["Volume"]),
                )
            )
        return bars

    def get_fundamentals(self, symbol: str) -> FundamentalsSnapshot:
        try:
            info = self._ticker(symbol).info
        except Exception as exc:  # pragma: no cover - network/library errors
            raise MarketDataError(f"Failed to fetch fundamentals for {symbol}: {exc}") from exc

        return FundamentalsSnapshot(
            symbol=symbol.upper(),
            long_name=info.get("longName") or info.get("shortName"),
            sector=info.get("sector"),
            industry=info.get("industry"),
            market_cap=info.get("marketCap"),
            trailing_pe=info.get("trailingPE"),
            forward_pe=info.get("forwardPE"),
            peg_ratio=info.get("pegRatio") or info.get("trailingPegRatio"),
            ev_to_ebitda=info.get("enterpriseToEbitda"),
            revenue_growth=info.get("revenueGrowth"),
            earnings_growth=info.get("earningsGrowth"),
            gross_margins=info.get("grossMargins"),
            operating_margins=info.get("operatingMargins"),
            return_on_equity=info.get("returnOnEquity"),
            free_cashflow=info.get("freeCashflow"),
            total_debt=info.get("totalDebt"),
            total_cash=info.get("totalCash"),
            as_of=dt.datetime.now(dt.timezone.utc),
        )

    def get_news(self, symbol: str, limit: int = 10) -> list[NewsItem]:
        try:
            raw_items = self._ticker(symbol).get_news(count=limit) or []
        except Exception as exc:  # pragma: no cover - network/library errors
            raise MarketDataError(f"Failed to fetch news for {symbol}: {exc}") from exc

        items: list[NewsItem] = []
        for raw in raw_items[:limit]:
            content = raw.get("content", raw)
            url = (
                (content.get("canonicalUrl") or {}).get("url")
                or (content.get("clickThroughUrl") or {}).get("url")
                or ""
            )
            pub_date = content.get("pubDate")
            published_at = (
                dt.datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                if pub_date
                else dt.datetime.now(dt.timezone.utc)
            )
            items.append(
                NewsItem(
                    headline=content.get("title", ""),
                    source_url=url,
                    published_at=published_at,
                    category=content.get("contentType"),
                    summary=content.get("summary"),
                )
            )
        return items


def get_market_data_provider() -> MarketDataProvider:
    """Factory returning the configured MarketDataProvider (§config.market_data_provider)."""
    from catalystiq.config import get_settings

    provider_name = get_settings().market_data_provider
    if provider_name == "yahoo":
        return YahooFinanceProvider()
    raise ValueError(f"Unknown market data provider: {provider_name}")
