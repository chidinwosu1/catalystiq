"""Nasdaq Trader provider (§12): the free, keyless symbol-directory files.

NasdaqTraderProvider parses the pipe-delimited `nasdaqlisted.txt` and
`otherlisted.txt` symbol directories over the shared HTTP transport and
normalizes them into SecurityMasterEntry rows. Symbols are given a stable
internal id rather than being keyed on ticker alone, since a ticker can
change or be reused (§12).

The internal id here is a deterministic `{listing_market}:{symbol}` - a
stable-enough key for this build; mapping to a permanent external id
(CUSIP/FIGI) is a documented follow-up.
"""
from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod

from catalystiq.providers.base import DataDomain
from catalystiq.providers.transport import HttpTransport, RateLimiter
from catalystiq.schemas.regulatory import SecurityMasterEntry

_NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
_OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"


def _parse_pipe(text: str) -> tuple[list[str], list[list[str]]]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return [], []
    header = [h.strip().lower() for h in lines[0].split("|")]
    rows = []
    for ln in lines[1:]:
        # The trailer line is "File Creation Time: ..." with no pipes / a
        # leading marker - skip anything that isn't a real data row.
        if ln.startswith("File Creation Time") or "|" not in ln:
            continue
        rows.append([c.strip() for c in ln.split("|")])
    return header, rows


def _yes(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    return value.strip().upper() == "Y"


class SecurityMasterProvider(ABC):
    @abstractmethod
    def get_securities(self) -> list[SecurityMasterEntry]: ...


class NasdaqTraderProvider(SecurityMasterProvider):
    PROVIDER_NAME = "nasdaq_trader"
    ADAPTER_VERSION = "1.0.0"
    DOMAIN = DataDomain.REGULATORY

    def __init__(self, transport: HttpTransport | None = None) -> None:
        self._transport = transport or HttpTransport(
            self.PROVIDER_NAME, rate_limiter=RateLimiter(rate_per_sec=1.0)
        )

    def _fetch(self, url: str) -> str:
        return self._transport.request("GET", url).raise_for_status().text

    @staticmethod
    def _internal_id(market: str | None, symbol: str) -> str:
        return f"{(market or 'UNKNOWN').upper()}:{symbol.upper()}"

    def get_securities(self) -> list[SecurityMasterEntry]:
        nasdaq = self.parse_nasdaq_listed(self._fetch(_NASDAQ_LISTED_URL))
        other = self.parse_other_listed(self._fetch(_OTHER_LISTED_URL))
        return nasdaq + other

    def parse_nasdaq_listed(self, text: str) -> list[SecurityMasterEntry]:
        header, rows = _parse_pipe(text)
        idx = {name: i for i, name in enumerate(header)}
        retrieved = dt.datetime.now(dt.timezone.utc)
        out: list[SecurityMasterEntry] = []
        for cells in rows:
            def get(col):
                i = idx.get(col)
                return cells[i] if i is not None and i < len(cells) else None

            symbol = get("symbol")
            if not symbol:
                continue
            test_issue = _yes(get("test issue"))
            out.append(
                SecurityMasterEntry(
                    internal_security_id=self._internal_id("NASDAQ", symbol),
                    symbol=symbol.upper(),
                    name=get("security name"),
                    exchange="NASDAQ",
                    listing_market=get("market category"),
                    etf=_yes(get("etf")),
                    test_issue=test_issue,
                    is_active=not bool(test_issue),
                    source=self.PROVIDER_NAME,
                    retrieved_at=retrieved,
                )
            )
        return out

    def parse_other_listed(self, text: str) -> list[SecurityMasterEntry]:
        header, rows = _parse_pipe(text)
        idx = {name: i for i, name in enumerate(header)}
        retrieved = dt.datetime.now(dt.timezone.utc)
        out: list[SecurityMasterEntry] = []
        for cells in rows:
            def get(col):
                i = idx.get(col)
                return cells[i] if i is not None and i < len(cells) else None

            symbol = get("act symbol") or get("act symbol") or get("nasdaq symbol")
            if not symbol:
                continue
            exchange = get("exchange")
            test_issue = _yes(get("test issue"))
            out.append(
                SecurityMasterEntry(
                    internal_security_id=self._internal_id(exchange, symbol),
                    symbol=symbol.upper(),
                    name=get("security name"),
                    exchange=exchange,
                    listing_market=exchange,
                    etf=_yes(get("etf")),
                    test_issue=test_issue,
                    is_active=not bool(test_issue),
                    source=self.PROVIDER_NAME,
                    retrieved_at=retrieved,
                )
            )
        return out


def get_nasdaq_trader_provider() -> NasdaqTraderProvider:
    return NasdaqTraderProvider()
