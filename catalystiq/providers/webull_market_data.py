"""Webull OpenAPI Market Data adapter for the real-time Entry Check feed.

This is a DEDICATED intraday/quote source, separate from the daily-history
``MarketDataProvider`` (Yahoo). It exists so the Entry Quality / Entry Check
feature can run on Webull's real-time Level-1 US quotes and 1m/5m candlestick
bars instead of Yahoo's ~15-min-delayed, rate-limited scraping path — while the
daily Silver pipeline, fundamentals and news stay on Yahoo untouched.

It reuses the SAME Webull OpenAPI app credentials the trade adapter already uses
(``webull_app_key`` / ``webull_app_secret`` / ``webull_region_id``). The market-
data client ships in the existing ``webull-openapi-python-sdk`` dependency under
``webull.data`` (the trade adapter uses ``webull.core`` / ``webull.trade``).

SDK BINDING NOTE (must be verified against the installed SDK version, 2.x):
Per Webull's Python SDK, market data is reached via
``from webull.data.data_client import DataClient`` and
``data_client.market_data.get_history_bar(...)`` (single symbol) — the same
signed ``ApiClient(app_key, app_secret, region_id)`` construction as the trade
client, and the same ``response.status_code`` / ``response.json()`` shape. The
exact keyword-argument and response-field NAMES can vary by SDK minor version,
so every SDK call here is wrapped and every field is read TOLERANTLY (several
candidate keys) — any mismatch surfaces as a ``MarketDataError`` and the Entry
Check honestly degrades to insufficient_data, never a crash or fabricated value.
The one runtime prerequisite this code cannot self-verify is that the Webull app
is entitlement-enabled for market data (Advanced Quotes Center).
"""
from __future__ import annotations

import datetime as dt

from catalystiq.providers.base import DataDomain
from catalystiq.providers.fetch_tracker import record_fetch
from catalystiq.providers.market_data import MarketDataError, MarketDataProvider
from catalystiq.schemas.market_data import (
    FundamentalsSnapshot,
    IntradayBar,
    NewsItem,
    OHLCVBar,
    Quote,
)

# Our interval string -> Webull "timespan" code. Webull uses m1/m5/m15/... for
# minute bars and d1 for daily. Kept small and explicit.
_TIMESPAN_BY_INTERVAL: dict[str, str] = {
    "1m": "m1",
    "5m": "m5",
    "15m": "m15",
    "30m": "m30",
    "1h": "h1",
    "1d": "d1",
}

# Webull categorizes instruments; US equities are "US_STOCK". Isolated so it's
# easy to extend (ETF/ADR share this category on Webull).
_US_EQUITY_CATEGORY = "US_STOCK"

# Bars per session used only to size the `count` request from a `days` window
# (78 five-minute RTH bars/day; padded). Never used to fabricate data.
_BARS_PER_SESSION = 80


def _first(d: dict, *keys, default=None):
    """Read the first present, non-None key from a dict (tolerant field names)."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _to_utc(ts) -> dt.datetime | None:
    """Parse a Webull bar timestamp (epoch seconds/millis or ISO string) to an
    aware UTC datetime. Returns None if it can't be parsed (row is then skipped
    rather than guessed)."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        secs = float(ts)
        if secs > 1e12:  # milliseconds
            secs /= 1000.0
        return dt.datetime.fromtimestamp(secs, tz=dt.timezone.utc)
    if isinstance(ts, str):
        s = ts.strip()
        if s.isdigit():
            return _to_utc(int(s))
        try:
            parsed = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    return None


def _rows_from_payload(payload) -> list[dict]:
    """Normalize the SDK response body into a list of bar-row dicts. Webull may
    return a bare list, or a dict wrapping the rows under a data/bars/candles
    key — handle both without assuming one shape."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("data", "bars", "candles", "list", "rows"):
            v = payload.get(key)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
        # A single-symbol dict that itself already looks like a bar container.
        rows = payload.get("candle") or payload.get("klines")
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    return []


def _parse_bar(row: dict) -> IntradayBar | None:
    ts = _to_utc(_first(row, "timestamp", "timeStamp", "time", "t", "tradeTime"))
    o = _first(row, "open", "o", "openPrice")
    h = _first(row, "high", "h", "highPrice")
    low = _first(row, "low", "l", "lowPrice")
    c = _first(row, "close", "c", "closePrice")
    v = _first(row, "volume", "v", "vol", default=0)
    if ts is None or None in (o, h, low, c):
        return None
    try:
        return IntradayBar(
            timestamp=ts, open=float(o), high=float(h), low=float(low),
            close=float(c), volume=int(float(v or 0)),
        )
    except (TypeError, ValueError):
        return None


class WebullMarketDataProvider(MarketDataProvider):
    """Real-time quotes + intraday bars from Webull OpenAPI Market Data.

    Only the market-data surface is implemented: ``get_quote``,
    ``get_ohlcv`` and ``get_intraday_ohlcv``. ``get_fundamentals`` /
    ``get_news`` are not part of the market-data API and raise ``MarketDataError``
    (this provider is used ONLY for the intraday Entry Check feed, never for the
    daily pipeline / fundamentals / news, which stay on Yahoo)."""

    PROVIDER_NAME = "webull_mdata"
    DOMAIN = DataDomain.MARKET_DATA
    ADAPTER_VERSION = "1.0.0"

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        region_id: str = "us",
        api_endpoint: str = "",
        *,
        client=None,
    ) -> None:
        app_key = (app_key or "").strip()
        app_secret = (app_secret or "").strip()
        region_id = (region_id or "us").strip() or "us"
        self._region_id = region_id

        if client is not None:
            # Injected client (tests / alternative transport).
            self._data_client = client
        else:
            if not app_key or not app_secret:
                raise MarketDataError("Webull market-data app_key/app_secret are not configured.")
            try:
                from webull.core.client import ApiClient
                from webull.data.data_client import DataClient

                api_client = ApiClient(app_key, app_secret, region_id)
                if api_endpoint:
                    api_client.add_endpoint(region_id, api_endpoint.strip())
                self._data_client = DataClient(api_client)
            except MarketDataError:
                raise
            except Exception as exc:
                raise MarketDataError(
                    f"Failed to initialize the Webull market-data client: {exc}"
                ) from exc

    # -- SDK access (isolated so a binding change touches one place) ---------

    @property
    def _md(self):
        """The market-data sub-client. Some SDK versions expose it as
        ``data_client.market_data``; others put the methods on the client
        directly — tolerate both."""
        return getattr(self._data_client, "market_data", self._data_client)

    def _call(self, method_names: tuple[str, ...], **kwargs):
        md = self._md
        method = next((getattr(md, n) for n in method_names if hasattr(md, n)), None)
        if method is None:
            raise MarketDataError(
                f"Webull market-data client exposes none of {method_names!r}."
            )
        try:
            response = method(**kwargs)
        except Exception as exc:
            raise MarketDataError(f"Webull market-data call failed: {exc}") from exc
        return self._body(response)

    @staticmethod
    def _body(response):
        """Extract the JSON body from an SDK response (or pass a plain
        dict/list through, for injected test clients)."""
        if isinstance(response, (list, dict)):
            return response
        status = getattr(response, "status_code", 200)
        if status != 200:
            text = getattr(response, "text", "")
            raise MarketDataError(f"Webull market-data API error {status}: {text}")
        try:
            return response.json()
        except Exception as exc:
            raise MarketDataError(f"Webull market-data response was not JSON: {exc}") from exc

    # -- MarketDataProvider surface ------------------------------------------

    def get_intraday_ohlcv(
        self, symbol: str, *, interval: str = "5m", days: int = 20
    ) -> list[IntradayBar]:
        timespan = _TIMESPAN_BY_INTERVAL.get(interval)
        if timespan is None:
            raise MarketDataError(f"Unsupported Webull intraday interval {interval!r}")
        count = max(1, days) * _BARS_PER_SESSION
        payload = self._call(
            ("get_history_bar", "get_bars", "get_history_bars"),
            symbol=symbol.upper(), category=_US_EQUITY_CATEGORY,
            timespan=timespan, count=count,
        )
        record_fetch(self.PROVIDER_NAME)
        bars = [b for b in (_parse_bar(r) for r in _rows_from_payload(payload)) if b]
        bars.sort(key=lambda b: b.timestamp)
        return bars

    def get_ohlcv(
        self, symbol: str, start: dt.date, end: dt.date | None = None, interval: str = "1d"
    ) -> list[OHLCVBar]:
        intraday = self.get_intraday_ohlcv(
            symbol, interval=interval if interval in _TIMESPAN_BY_INTERVAL else "1d", days=400
        )
        end = end or dt.date.today()
        out: list[OHLCVBar] = []
        for b in intraday:
            d = b.timestamp.date()
            if d < start or d > end:
                continue
            out.append(OHLCVBar(date=d, open=b.open, high=b.high, low=b.low,
                                 close=b.close, volume=b.volume))
        return out

    def get_quote(self, symbol: str) -> Quote:
        payload = self._call(
            ("get_snapshot", "get_quote", "get_snapshots"),
            symbol=symbol.upper(), category=_US_EQUITY_CATEGORY,
        )
        rows = _rows_from_payload(payload)
        row = rows[0] if rows else (payload if isinstance(payload, dict) else {})
        price = _first(row, "close", "price", "last", "lastPrice", "deal", "tradePrice")
        if price is None:
            raise MarketDataError(f"No Webull quote available for {symbol}.")
        prev = _first(row, "preClose", "previousClose", "prevClose")
        record_fetch(self.PROVIDER_NAME)
        return Quote(
            symbol=symbol.upper(), price=float(price),
            previous_close=float(prev) if prev is not None else None,
            as_of=dt.datetime.now(dt.timezone.utc),
        )

    def get_fundamentals(self, symbol: str) -> FundamentalsSnapshot:
        raise MarketDataError("Webull market-data adapter does not provide fundamentals.")

    def get_news(self, symbol: str, limit: int = 10) -> list[NewsItem]:
        raise MarketDataError("Webull market-data adapter does not provide news.")
