"""BLS provider (§8): the official BLS Public Data API v2.

BlsProvider normalizes BLS observations into the SAME MacroObservation shape
FRED uses, so downstream macro code is source-agnostic - while preserving
BLS-specific metadata (period code, footnotes, preliminary/revised status) in
the observation's `source_fields`. Series IDs are configured, not hardcoded
throughout the code (DEFAULT_BLS_SERIES here, overridable via settings).

BLS has no vintage/realtime concept, so realtime_start/end are None; a later
revision of the same period is detected via the footnote status and lands as
the same (provider, series, date) row updated in place - BLS itself only ever
exposes one current value per period.
"""
from __future__ import annotations

import datetime as dt

from catalystiq.providers.base import DataDomain, ProviderError, ProviderErrorCategory
from catalystiq.providers.macro import MacroDataProvider
from catalystiq.providers.transport import HttpTransport, RateLimiter
from catalystiq.schemas.macro import MacroObservation, MacroSeries

_BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# Configured series (label only, for reference/metadata). Not hardcoded into
# any calculation - the pipeline requests whatever ids it's given, defaulting
# to these. Extend/override via settings if needed.
DEFAULT_BLS_SERIES: dict[str, str] = {
    "CUUR0000SA0": "CPI-U, all items, NSA",
    "CUSR0000SA0": "CPI-U, all items, SA",
    "CUUR0000SA0L1E": "Core CPI (all items less food and energy), NSA",
    "WPUFD4": "PPI final demand",
    "CES0000000001": "Total nonfarm payrolls, SA",
    "LNS14000000": "Unemployment rate, SA",
    "CES0500000003": "Average hourly earnings, total private, SA",
    "JTS000000000000000JOL": "Job openings (JOLTS), SA",
    "CIU1010000000000A": "Employment Cost Index",
}

# Month/quarter/annual period codes -> (month, is_annual).
def _period_to_date(year: int, period: str) -> dt.date | None:
    period = period.upper()
    if period.startswith("M"):
        num = period[1:]
        if num == "13":  # annual average
            return dt.date(year, 1, 1)
        try:
            month = int(num)
        except ValueError:
            return None
        if 1 <= month <= 12:
            return dt.date(year, month, 1)
        return None
    if period.startswith("Q"):
        q = period[1:]
        mapping = {"01": 1, "02": 4, "03": 7, "04": 10, "05": 1}
        month = mapping.get(q)
        return dt.date(year, month, 1) if month else None
    if period.startswith("A"):  # annual
        return dt.date(year, 1, 1)
    if period.startswith("S"):  # semiannual: S01->H1, S02->H2, S03->annual
        month = {"01": 1, "02": 7, "03": 1}.get(period[1:])
        return dt.date(year, month, 1) if month else None
    return None


class BlsProvider(MacroDataProvider):
    PROVIDER_NAME = "bls"
    ADAPTER_VERSION = "1.0.0"
    DOMAIN = DataDomain.MACRO

    def __init__(self, api_key: str, transport: HttpTransport | None = None) -> None:
        if not api_key:
            raise ProviderError(
                "BLS api_key is not configured.",
                category=ProviderErrorCategory.CONFIG,
                provider=self.PROVIDER_NAME,
            )
        self._api_key = api_key
        # BLS registered-key limit is generous (500/day); keep a modest rate.
        self._transport = transport or HttpTransport(
            self.PROVIDER_NAME, rate_limiter=RateLimiter(rate_per_sec=1.0)
        )

    def get_series(self, series_id: str) -> MacroSeries:
        return MacroSeries(
            series_id=series_id,
            title=DEFAULT_BLS_SERIES.get(series_id),
            source=self.PROVIDER_NAME,
            retrieved_at=dt.datetime.now(dt.timezone.utc),
        )

    def get_observations(
        self,
        series_id: str,
        observation_start: dt.date | None = None,
        observation_end: dt.date | None = None,
        as_of: dt.date | None = None,
    ) -> list[MacroObservation]:
        start_year = observation_start.year if observation_start else dt.date.today().year - 10
        end_year = observation_end.year if observation_end else dt.date.today().year
        return self.get_observations_batch([series_id], start_year, end_year).get(series_id, [])

    def get_observations_batch(
        self, series_ids: list[str], start_year: int, end_year: int
    ) -> dict[str, list[MacroObservation]]:
        """Batch request (BLS allows up to 50 series per POST with a key)."""
        body = {
            "seriesid": series_ids,
            "startyear": str(start_year),
            "endyear": str(end_year),
            "registrationkey": self._api_key,
            "annualaverage": True,
        }
        resp = self._transport.request("POST", _BLS_URL, json=body).raise_for_status()
        data = resp.json()
        if data.get("status") != "REQUEST_SUCCEEDED":
            messages = "; ".join(data.get("message", []) or [])
            raise ProviderError(
                f"BLS request did not succeed: {messages or data.get('status')}",
                category=ProviderErrorCategory.UNAVAILABLE,
                provider=self.PROVIDER_NAME,
            )

        retrieved = dt.datetime.now(dt.timezone.utc)
        out: dict[str, list[MacroObservation]] = {}
        for series in (data.get("Results") or {}).get("series", []):
            sid = series.get("seriesID")
            obs_list: list[MacroObservation] = []
            for row in series.get("data", []):
                try:
                    year = int(row.get("year"))
                except (TypeError, ValueError):
                    continue
                obs_date = _period_to_date(year, row.get("period", ""))
                if obs_date is None:
                    continue
                footnotes = [
                    f for f in (row.get("footnotes") or []) if f and f.get("code")
                ]
                preliminary = any(f.get("code") == "P" for f in footnotes)
                obs_list.append(
                    MacroObservation(
                        series_id=sid,
                        observation_date=obs_date,
                        value=_as_float(row.get("value")),
                        source=self.PROVIDER_NAME,
                        source_fields={
                            "period": row.get("period"),
                            "period_name": row.get("periodName"),
                            "footnotes": footnotes or None,
                            "preliminary": preliminary,
                        },
                        retrieved_at=retrieved,
                    )
                )
            out[sid] = obs_list
        return out


def _as_float(value) -> float | None:
    if value is None or value in ("", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def get_bls_provider() -> BlsProvider:
    from catalystiq.config import get_settings

    return BlsProvider(get_settings().bls_api_key)
