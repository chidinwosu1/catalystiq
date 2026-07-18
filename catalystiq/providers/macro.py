"""Macro-data provider interface (§7, §9) and the FRED/ALFRED implementation.

FredProvider talks to the official FRED REST API over the shared HTTP
transport (timeouts, retries, rate limiting, secret redaction), so the API
key never reaches a log line. ALFRED point-in-time retrieval is supported
through FRED's realtime_start/realtime_end parameters: pass a historical
`as_of` date to get the vintage that was known then, instead of the latest
revised value.

The adapter returns normalized MacroSeries/MacroObservation/EconomicRelease
objects and never computes anything. A missing observation (FRED sends ".")
becomes value=None, never a fabricated number.
"""
from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod

from catalystiq.providers.base import (
    DataDomain,
    ProviderError,
    ProviderErrorCategory,
)
from catalystiq.providers.transport import HttpTransport, RateLimiter
from catalystiq.schemas.macro import EconomicRelease, MacroObservation, MacroSeries

_FRED_BASE = "https://api.stlouisfed.org/fred"


class MacroDataProvider(ABC):
    @abstractmethod
    def get_series(self, series_id: str) -> MacroSeries: ...

    @abstractmethod
    def get_observations(
        self,
        series_id: str,
        observation_start: dt.date | None = None,
        observation_end: dt.date | None = None,
        as_of: dt.date | None = None,
    ) -> list[MacroObservation]: ...


def _parse_date(value: str | None) -> dt.date | None:
    if not value or value in (".", ""):
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _parse_value(value: str | None) -> float | None:
    # FRED encodes a missing observation as ".".
    if value is None or value == "." or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


class FredProvider(MacroDataProvider):
    PROVIDER_NAME = "fred"
    ADAPTER_VERSION = "1.0.0"
    DOMAIN = DataDomain.MACRO

    def __init__(self, api_key: str, transport: HttpTransport | None = None) -> None:
        if not api_key:
            raise ProviderError(
                "FRED api_key is not configured.",
                category=ProviderErrorCategory.CONFIG,
                provider=self.PROVIDER_NAME,
            )
        self._api_key = api_key
        # FRED permits 120 requests/minute; stay well under with a token
        # bucket (~2/sec). Injectable transport keeps tests offline.
        self._transport = transport or HttpTransport(
            self.PROVIDER_NAME,
            base_url=_FRED_BASE,
            rate_limiter=RateLimiter(rate_per_sec=2.0),
        )

    def _get(self, path: str, params: dict) -> dict:
        params = {**params, "api_key": self._api_key, "file_type": "json"}
        resp = self._transport.request("GET", path, params=params).raise_for_status()
        return resp.json()

    def get_series(self, series_id: str) -> MacroSeries:
        data = self._get("series", {"series_id": series_id})
        items = data.get("seriess") or []
        if not items:
            raise ProviderError(
                f"FRED series {series_id!r} not found.",
                category=ProviderErrorCategory.NOT_FOUND,
                provider=self.PROVIDER_NAME,
            )
        s = items[0]
        return MacroSeries(
            series_id=s.get("id", series_id),
            title=s.get("title"),
            frequency=s.get("frequency_short") or s.get("frequency"),
            units=s.get("units_short") or s.get("units"),
            seasonal_adjustment=s.get("seasonal_adjustment_short") or s.get("seasonal_adjustment"),
            observation_start=_parse_date(s.get("observation_start")),
            observation_end=_parse_date(s.get("observation_end")),
            last_updated=None,  # FRED's last_updated carries a tz offset; left unparsed here
            notes=s.get("notes"),
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
        params: dict = {"series_id": series_id}
        if observation_start:
            params["observation_start"] = observation_start.isoformat()
        if observation_end:
            params["observation_end"] = observation_end.isoformat()
        if as_of:
            # ALFRED point-in-time: the vintage known on `as_of`.
            params["realtime_start"] = as_of.isoformat()
            params["realtime_end"] = as_of.isoformat()

        data = self._get("series/observations", params)
        units = data.get("units")
        retrieved = dt.datetime.now(dt.timezone.utc)
        out: list[MacroObservation] = []
        for obs in data.get("observations", []):
            obs_date = _parse_date(obs.get("date"))
            if obs_date is None:
                continue
            out.append(
                MacroObservation(
                    series_id=series_id,
                    observation_date=obs_date,
                    value=_parse_value(obs.get("value")),
                    realtime_start=_parse_date(obs.get("realtime_start")),
                    realtime_end=_parse_date(obs.get("realtime_end")),
                    units=units,
                    source=self.PROVIDER_NAME,
                    retrieved_at=retrieved,
                )
            )
        return out

    def get_releases(self) -> list[EconomicRelease]:
        data = self._get("releases", {})
        retrieved = dt.datetime.now(dt.timezone.utc)
        out: list[EconomicRelease] = []
        for rel in data.get("releases", []):
            out.append(
                EconomicRelease(
                    release_id=str(rel.get("id")),
                    name=rel.get("name"),
                    press_release=rel.get("press_release"),
                    link=rel.get("link"),
                    source=self.PROVIDER_NAME,
                    retrieved_at=retrieved,
                )
            )
        return out


def get_macro_provider() -> MacroDataProvider:
    """Factory for the configured macro provider (currently FRED)."""
    from catalystiq.config import get_settings

    return FredProvider(get_settings().fred_api_key)
