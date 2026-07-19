"""Fundamentals provider interface (§6) and the SEC EDGAR implementation.

SecEdgarProvider uses the official SEC endpoints over the shared HTTP
transport, with the SEC-required descriptive User-Agent set on every request
and a conservative rate limit (SEC fair-access asks for <=10 req/s; this
stays well under). It returns normalized SecurityIdentifier / CompanyFiling /
CompanyFact / MaterialEvent objects and computes nothing.

Endpoints used:
  - https://www.sec.gov/files/company_tickers.json   (ticker -> CIK)
  - https://data.sec.gov/submissions/CIK##########.json   (filings)
  - https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json   (XBRL facts)
"""
from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod

from catalystiq.providers.base import DataDomain, ProviderError, ProviderErrorCategory
from catalystiq.providers.transport import HttpTransport, RateLimiter
from catalystiq.schemas.fundamentals import (
    CompanyFact,
    CompanyFiling,
    MaterialEvent,
    SecurityIdentifier,
)

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"


def _pad_cik(cik: str | int) -> str:
    return str(int(cik)).zfill(10)


def _parse_date(value) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parse_dt(value) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class FundamentalsProvider(ABC):
    @abstractmethod
    def resolve_cik(self, symbol: str) -> SecurityIdentifier: ...

    @abstractmethod
    def get_filings(self, cik: str) -> list[CompanyFiling]: ...

    @abstractmethod
    def get_company_facts(self, cik: str) -> list[CompanyFact]: ...


class SecEdgarProvider(FundamentalsProvider):
    PROVIDER_NAME = "sec_edgar"
    ADAPTER_VERSION = "1.0.0"
    DOMAIN = DataDomain.FUNDAMENTALS

    def __init__(self, user_agent: str, transport: HttpTransport | None = None) -> None:
        if not user_agent:
            raise ProviderError(
                "SEC_USER_AGENT is not configured (SEC requires a descriptive "
                "User-Agent with contact info).",
                category=ProviderErrorCategory.CONFIG,
                provider=self.PROVIDER_NAME,
            )
        self._user_agent = user_agent
        # SEC fair-access: <=10 req/s. Stay conservative at 5/s.
        self._transport = transport or HttpTransport(
            self.PROVIDER_NAME,
            default_headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            rate_limiter=RateLimiter(rate_per_sec=5.0),
        )
        self._ticker_map: dict[str, dict] | None = None

    def _get_json(self, url: str):
        return self._transport.request("GET", url).raise_for_status().json()

    # --- ticker -> CIK ---------------------------------------------------

    def _load_ticker_map(self) -> dict[str, dict]:
        if self._ticker_map is None:
            data = self._get_json(_TICKERS_URL)
            # Keyed by row index; each row {cik_str, ticker, title}.
            self._ticker_map = {
                str(row["ticker"]).upper(): row for row in data.values() if row.get("ticker")
            }
        return self._ticker_map

    def resolve_cik(self, symbol: str) -> SecurityIdentifier:
        symbol = symbol.upper()
        row = self._load_ticker_map().get(symbol)
        if row is None:
            raise ProviderError(
                f"SEC EDGAR has no CIK for symbol {symbol!r}.",
                category=ProviderErrorCategory.NOT_FOUND,
                provider=self.PROVIDER_NAME,
            )
        return SecurityIdentifier(
            symbol=symbol,
            cik=_pad_cik(row["cik_str"]),
            name=row.get("title"),
            source=self.PROVIDER_NAME,
            retrieved_at=dt.datetime.now(dt.timezone.utc),
        )

    # --- filings ---------------------------------------------------------

    def get_filings(self, cik: str) -> list[CompanyFiling]:
        cik = _pad_cik(cik)
        data = self._get_json(_SUBMISSIONS_URL.format(cik=cik))
        recent = (data.get("filings") or {}).get("recent") or {}
        retrieved = dt.datetime.now(dt.timezone.utc)
        symbols = data.get("tickers") or []
        symbol = symbols[0] if symbols else None

        forms = recent.get("form", [])
        accns = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        acceptance = recent.get("acceptanceDateTime", [])
        report_dates = recent.get("reportDate", [])
        primary_docs = recent.get("primaryDocument", [])
        primary_descs = recent.get("primaryDocDescription", [])
        items = recent.get("items", [])

        out: list[CompanyFiling] = []
        for i in range(len(accns)):
            form = forms[i] if i < len(forms) else ""
            accn = accns[i]
            out.append(
                CompanyFiling(
                    cik=cik,
                    symbol=symbol,
                    form=form,
                    accession_number=accn,
                    filing_date=_parse_date(filing_dates[i]) if i < len(filing_dates) else None,
                    acceptance_datetime=_parse_dt(acceptance[i]) if i < len(acceptance) else None,
                    report_date=_parse_date(report_dates[i]) if i < len(report_dates) else None,
                    primary_document=primary_docs[i] if i < len(primary_docs) else None,
                    primary_doc_description=primary_descs[i] if i < len(primary_descs) else None,
                    is_amendment=form.endswith("/A"),
                    source_url=_filing_index_url(cik, accn),
                    source=self.PROVIDER_NAME,
                    retrieved_at=retrieved,
                )
            )
        return out

    def get_material_events(self, cik: str) -> list[MaterialEvent]:
        """8-K filings (and 8-K/A) extracted from the submissions feed."""
        cik = _pad_cik(cik)
        data = self._get_json(_SUBMISSIONS_URL.format(cik=cik))
        recent = (data.get("filings") or {}).get("recent") or {}
        retrieved = dt.datetime.now(dt.timezone.utc)
        symbols = data.get("tickers") or []
        symbol = symbols[0] if symbols else None

        forms = recent.get("form", [])
        accns = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        acceptance = recent.get("acceptanceDateTime", [])
        items = recent.get("items", [])

        out: list[MaterialEvent] = []
        for i in range(len(accns)):
            form = forms[i] if i < len(forms) else ""
            if not form.startswith("8-K"):
                continue
            raw_items = items[i] if i < len(items) else ""
            out.append(
                MaterialEvent(
                    cik=cik,
                    symbol=symbol,
                    accession_number=accns[i],
                    form=form,
                    filing_date=_parse_date(filing_dates[i]) if i < len(filing_dates) else None,
                    acceptance_datetime=_parse_dt(acceptance[i]) if i < len(acceptance) else None,
                    items=[s.strip() for s in str(raw_items).split(",") if s.strip()],
                    is_amendment=form.endswith("/A"),
                    source_url=_filing_index_url(cik, accns[i]),
                    source=self.PROVIDER_NAME,
                    retrieved_at=retrieved,
                )
            )
        return out

    # --- XBRL company facts ---------------------------------------------

    def get_company_facts(self, cik: str) -> list[CompanyFact]:
        cik = _pad_cik(cik)
        data = self._get_json(_FACTS_URL.format(cik=cik))
        retrieved = dt.datetime.now(dt.timezone.utc)
        facts: list[CompanyFact] = []
        for taxonomy, concepts in (data.get("facts") or {}).items():
            for concept, concept_body in concepts.items():
                for unit, datapoints in (concept_body.get("units") or {}).items():
                    for dp in datapoints:
                        form = dp.get("form")
                        facts.append(
                            CompanyFact(
                                cik=cik,
                                taxonomy=taxonomy,
                                concept=concept,
                                unit=unit,
                                value=_as_float(dp.get("val")),
                                fiscal_year=dp.get("fy"),
                                fiscal_period=dp.get("fp"),
                                period_start=_parse_date(dp.get("start")),
                                period_end=_parse_date(dp.get("end")),
                                form=form,
                                filing_date=_parse_date(dp.get("filed")),
                                accession_number=dp.get("accn"),
                                is_amendment=bool(form) and form.endswith("/A"),
                                frame=dp.get("frame"),
                                source=self.PROVIDER_NAME,
                                retrieved_at=retrieved,
                            )
                        )
        return facts


def _as_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _filing_index_url(cik: str, accession_number: str) -> str:
    accn_nodash = accession_number.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn_nodash}/"
        f"{accession_number}-index.htm"
    )


def get_fundamentals_provider() -> "SecEdgarProvider":
    """Factory for the configured fundamentals provider (SEC EDGAR)."""
    from catalystiq.config import get_settings

    return SecEdgarProvider(get_settings().sec_user_agent)
