"""Ephemeral FRED API client (compliance requirements #1, #3, #4, #7).

This is the ONLY thing that talks to FRED, and it does so exclusively through
the official, documented REST API over the shared HTTP transport (timeouts,
retries, rate limiting, secret redaction) - never by scraping or bulk
download. It takes no database session, holds no cache, and returns plain
in-memory objects that the service layer renders and then discards. Nothing
here is persisted, logged as a body, or fed to any model/score/order path.

The API key is read from backend settings and passed to the transport, which
redacts it from every log line. It is never returned in a response.
"""
from __future__ import annotations

import datetime as dt

from catalystiq.providers.base import (
    DataDomain,
    ProviderError,
    ProviderErrorCategory,
)
from catalystiq.providers.transport import HttpTransport, RateLimiter
from catalystiq.schemas.macro import MacroObservation, MacroSeries

_FRED_BASE = "https://api.stlouisfed.org/fred"


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


class FredClient:
    """Stateless, ephemeral FRED reader. Construct per request; discard after."""

    PROVIDER_NAME = "fred"
    # Distinct from the old persisted adapter: this build never stores.
    ADAPTER_VERSION = "2.0.0-ephemeral"
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
        params: dict = {"series_id": series_id, "sort_order": "asc"}
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


def get_fred_client() -> FredClient:
    """Construct a FRED client from backend settings (key never leaves here)."""
    from catalystiq.config import get_settings

    return FredClient(get_settings().fred_api_key)
