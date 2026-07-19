"""FINRA provider (§11): daily short-sale volume and equity short interest,
kept as two SEPARATE datasets.

Daily short-sale volume comes from FINRA's free, keyless reg-SHO consolidated
files (pipe-delimited, no auth). Short interest is the semi-monthly equity
short-interest file. Both are parsed header-first so a column reorder in the
published file doesn't silently mismap fields.

Endpoint/format note: the reg-SHO daily short-sale-volume file URL and layout
are stable and well-known. The exact public URL and column layout of the
current short-interest file are less certain across FINRA's site revisions -
the parser here is header-driven and tested against a representative fixture;
confirm against the live file before relying on it in production.
"""
from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod

from catalystiq.providers.base import DataDomain, ProviderError, ProviderErrorCategory
from catalystiq.providers.transport import HttpTransport, RateLimiter
from catalystiq.schemas.regulatory import ShortInterest, ShortSaleVolume

# Consolidated NMS daily short-sale volume file.
_SHORT_VOLUME_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{yyyymmdd}.txt"


def _parse_header(text: str) -> tuple[list[str], list[str]]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return [], []
    header = [h.strip().lower() for h in lines[0].split("|")]
    return header, lines[1:]


def _pick(row: dict, *names: str) -> str | None:
    for n in names:
        if n in row and row[n] != "":
            return row[n]
    return None


def _as_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except ValueError:
        return None


def _as_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _as_date(value) -> dt.date | None:
    if not value:
        return None
    s = str(value).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


class RegulatoryProvider(ABC):
    @abstractmethod
    def get_short_sale_volume(self, trade_date: dt.date) -> list[ShortSaleVolume]: ...


class FinraProvider(RegulatoryProvider):
    PROVIDER_NAME = "finra"
    ADAPTER_VERSION = "1.0.0"
    DOMAIN = DataDomain.REGULATORY

    def __init__(self, transport: HttpTransport | None = None) -> None:
        # Keyless. Modest rate limit to be a good citizen.
        self._transport = transport or HttpTransport(
            self.PROVIDER_NAME, rate_limiter=RateLimiter(rate_per_sec=2.0)
        )

    def _fetch(self, url: str) -> str:
        resp = self._transport.request("GET", url).raise_for_status()
        return resp.text

    def get_short_sale_volume(
        self, trade_date: dt.date, file_version: str = "original"
    ) -> list[ShortSaleVolume]:
        url = _SHORT_VOLUME_URL.format(yyyymmdd=trade_date.strftime("%Y%m%d"))
        text = self._fetch(url)
        return self.parse_short_sale_volume(text, file_version=file_version)

    def parse_short_sale_volume(
        self, text: str, file_version: str = "original"
    ) -> list[ShortSaleVolume]:
        header, rows = _parse_header(text)
        if not header:
            return []
        retrieved = dt.datetime.now(dt.timezone.utc)
        out: list[ShortSaleVolume] = []
        for line in rows:
            cells = line.split("|")
            if len(cells) < 2:
                continue
            row = dict(zip(header, [c.strip() for c in cells]))
            symbol = _pick(row, "symbol")
            trade_date = _as_date(_pick(row, "date"))
            if not symbol or trade_date is None:
                continue  # skip footer/summary lines
            out.append(
                ShortSaleVolume(
                    symbol=symbol.upper(),
                    trade_date=trade_date,
                    short_volume=_as_int(_pick(row, "shortvolume")),
                    short_exempt_volume=_as_int(_pick(row, "shortexemptvolume")),
                    total_volume=_as_int(_pick(row, "totalvolume")),
                    reporting_facility=_pick(row, "market"),
                    file_version=file_version,
                    source=self.PROVIDER_NAME,
                    retrieved_at=retrieved,
                )
            )
        return out

    def parse_short_interest(
        self, text: str, file_version: str = "original"
    ) -> list[ShortInterest]:
        """Header-driven parse of the equity short-interest file. Tolerant of
        FINRA's column naming variants."""
        header, rows = _parse_header(text)
        if not header:
            return []
        retrieved = dt.datetime.now(dt.timezone.utc)
        out: list[ShortInterest] = []
        for line in rows:
            cells = line.split("|")
            if len(cells) < 2:
                continue
            row = dict(zip(header, [c.strip() for c in cells]))
            symbol = _pick(row, "symbolcode", "symbol", "issuesymbolidentifier")
            settlement = _as_date(_pick(row, "settlementdate", "settlement date"))
            if not symbol or settlement is None:
                continue
            out.append(
                ShortInterest(
                    symbol=symbol.upper(),
                    settlement_date=settlement,
                    publication_date=_as_date(
                        _pick(row, "publicationdate", "publication date", "issuedate")
                    ),
                    short_interest_quantity=_as_int(
                        _pick(row, "currentshortpositionquantity", "shortinterest", "current")
                    ),
                    previous_short_interest_quantity=_as_int(
                        _pick(row, "previousshortpositionquantity", "previous")
                    ),
                    average_daily_volume=_as_float(
                        _pick(row, "averagedailyvolumequantity", "avgdailyvolume")
                    ),
                    days_to_cover=_as_float(_pick(row, "daystocoverquantity", "daystocover")),
                    file_version=file_version,
                    source=self.PROVIDER_NAME,
                    retrieved_at=retrieved,
                )
            )
        return out


def get_finra_provider() -> FinraProvider:
    return FinraProvider()
