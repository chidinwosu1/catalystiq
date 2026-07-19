"""BEA provider (§9): the official BEA API (GetData).

BeaProvider fetches table-oriented BEA data over the shared HTTP transport
and normalizes each cell into a BeaValue, preserving the dataset, table, line,
period, unit, and scale so nominal/real/annualized/SA values are never merged
without their classification. Which tables to pull is configured
(DEFAULT_BEA_TABLES here, overridable via settings), not hardcoded into
calculations.
"""
from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod

from catalystiq.providers.base import DataDomain, ProviderError, ProviderErrorCategory
from catalystiq.providers.transport import HttpTransport, RateLimiter
from catalystiq.schemas.bea import BeaValue

_BEA_URL = "https://apps.bea.gov/api/data"

# dataset:table:frequency tuples to pull by default. GDP, personal income,
# PCE, corporate profits (all NIPA). Extend/override via settings.
DEFAULT_BEA_TABLES: list[tuple[str, str, str]] = [
    ("NIPA", "T10101", "Q"),  # Percent change in real GDP
    ("NIPA", "T10105", "Q"),  # GDP (levels)
    ("NIPA", "T20100", "Q"),  # Personal income and its disposition
    ("NIPA", "T20600", "M"),  # Personal income (monthly)
    ("NIPA", "T11200", "Q"),  # Corporate profits
]


class BeaProviderBase(ABC):
    @abstractmethod
    def get_table(self, dataset: str, table_name: str, frequency: str, year: str = "ALL") -> list[BeaValue]: ...


class BeaProvider(BeaProviderBase):
    PROVIDER_NAME = "bea"
    ADAPTER_VERSION = "1.0.0"
    DOMAIN = DataDomain.MACRO

    def __init__(self, api_key: str, transport: HttpTransport | None = None) -> None:
        if not api_key:
            raise ProviderError(
                "BEA api_key is not configured.",
                category=ProviderErrorCategory.CONFIG,
                provider=self.PROVIDER_NAME,
            )
        self._api_key = api_key
        self._transport = transport or HttpTransport(
            self.PROVIDER_NAME, rate_limiter=RateLimiter(rate_per_sec=1.0)
        )

    def get_table(
        self, dataset: str, table_name: str, frequency: str, year: str = "ALL"
    ) -> list[BeaValue]:
        params = {
            "UserID": self._api_key,
            "method": "GetData",
            "datasetname": dataset,
            "TableName": table_name,
            "Frequency": frequency,
            "Year": year,
            "ResultFormat": "JSON",
        }
        resp = self._transport.request("GET", _BEA_URL, params=params).raise_for_status()
        payload = resp.json()
        results = (payload.get("BEAAPI") or {}).get("Results") or {}

        # BEA reports request errors inside a 200 body.
        error = results.get("Error") or (payload.get("BEAAPI") or {}).get("Error")
        if error:
            detail = error.get("APIErrorDescription") if isinstance(error, dict) else str(error)
            raise ProviderError(
                f"BEA error: {detail}",
                category=ProviderErrorCategory.UNAVAILABLE,
                provider=self.PROVIDER_NAME,
            )

        retrieved = dt.datetime.now(dt.timezone.utc)
        out: list[BeaValue] = []
        for row in results.get("Data", []) or []:
            out.append(
                BeaValue(
                    dataset=dataset,
                    table_name=table_name,
                    line_number=str(row.get("LineNumber")) if row.get("LineNumber") else None,
                    line_description=row.get("LineDescription"),
                    series_code=row.get("SeriesCode"),
                    time_period=row.get("TimePeriod", ""),
                    frequency=frequency,
                    value=_as_float(row.get("DataValue")),
                    unit=row.get("CL_UNIT"),
                    scale=str(row.get("UNIT_MULT")) if row.get("UNIT_MULT") is not None else None,
                    note_ref=row.get("NoteRef"),
                    source=self.PROVIDER_NAME,
                    retrieved_at=retrieved,
                )
            )
        return out


def _as_float(value) -> float | None:
    if value is None or value in ("", "(NA)", "(D)", "..."):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def get_bea_provider() -> BeaProvider:
    from catalystiq.config import get_settings

    return BeaProvider(get_settings().bea_api_key)
