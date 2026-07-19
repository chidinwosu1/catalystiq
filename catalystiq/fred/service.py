"""Rule-Based Macroeconomic Context service (compliance requirements #1-#8).

This is the only orchestration point for FRED. It fetches the allowlisted,
public-domain indicators through the ephemeral FredClient, attaches the
required attribution + notice to every value, and returns a plain dict that
the /fred router serves with `Cache-Control: no-store`. It:

  - retrieves ONLY series that pass `require_retrievable` (allowlist + copyright
    gate) - a blocked or unknown series is never fetched;
  - takes NO database session and writes nothing anywhere - values live only in
    the returned object for the duration of the request;
  - degrades gracefully - if FRED is disabled/unconfigured the panel reports
    `available: False` and the rest of the app is unaffected (kill switch);
  - is deterministic and rule-based: it selects a fixed allowlist and reports
    the values as-is. It computes no score, probability, ranking, or
    recommendation, and feeds nothing into any model or order path.
"""
from __future__ import annotations

import datetime as dt

from catalystiq.fred.allowlist import (
    ALLOWLIST,
    SeriesBlocked,
    SeriesNotAllowed,
    approved_series,
    require_retrievable,
)
from catalystiq.fred.provider import FredClient
from catalystiq.providers.base import ProviderError

PANEL_NAME = "Rule-Based Macroeconomic Context"

# Required verbatim by the FRED API terms (requirement #6). Displayed on every
# screen showing FRED data and returned in every payload.
REQUIRED_NOTICE = (
    "This product uses the FRED® API but is not endorsed or certified by the "
    "Federal Reserve Bank of St. Louis."
)
DISCLAIMER = (
    "Informational macroeconomic context only. Not investment advice, and not "
    "guaranteed to be accurate, complete, or current."
)
# Terms reviewed for this integration (requirement #10: record date + URL).
TERMS_REVIEWED_URL = "https://fred.stlouisfed.org/docs/api/terms_of_use.html"
TERMS_REVIEWED_DATE = "2026-07-19"

# Bounded lookback so a single request stays small and nothing is bulk-pulled.
_LOOKBACK_DAYS = 1100
_RECENT_POINTS = 12


def context_available(settings) -> bool:
    """FRED context is available only when explicitly enabled AND configured.
    Default is off (secure-by-default kill switch, requirement #8)."""
    return bool(getattr(settings, "enable_fred", False)) and bool(
        getattr(settings, "fred_api_key", "")
    )


def _indicator_base(spec) -> dict:
    return {
        "series_id": spec.series_id,
        "title": spec.title,
        "owner": spec.owner,
        "attribution": spec.attribution,
        "purpose": spec.purpose,
        "units": spec.units,
        "frequency": spec.frequency,
        "status": "pending",
    }


def _envelope(as_of: dt.date | None) -> dict:
    """The attribution/notice envelope carried by every context response."""
    return {
        "panel": PANEL_NAME,
        "notice": REQUIRED_NOTICE,
        "disclaimer": DISCLAIMER,
        "terms_reviewed_url": TERMS_REVIEWED_URL,
        "terms_reviewed_date": TERMS_REVIEWED_DATE,
        "as_of": as_of.isoformat() if as_of else None,
        "ephemeral": True,  # not stored; served no-store
    }


def build_context(
    settings, as_of: dt.date | None = None, transport=None
) -> dict:
    """Fetch and render the allowlisted macro indicators, ephemerally.

    Never raises for a single-series outage - that indicator is marked
    `unavailable` (with the error CATEGORY only, never a response body) and the
    rest still render. Returns `available: False` when FRED is off."""
    result = _envelope(as_of)
    result["indicators"] = []

    if not context_available(settings):
        result["available"] = False
        result["reason"] = (
            "FRED is disabled or not configured. This optional macro-context "
            "panel is off; the rest of the app is unaffected."
        )
        return result

    result["available"] = True
    client = FredClient(settings.fred_api_key, transport=transport)
    start = (as_of or dt.date.today()) - dt.timedelta(days=_LOOKBACK_DAYS)

    for spec in approved_series():
        indicator = _indicator_base(spec)
        try:
            observations = client.get_observations(
                spec.series_id, observation_start=start, as_of=as_of
            )
        except ProviderError as exc:
            # Report the category only - never the response body (requirement #3).
            indicator["status"] = "unavailable"
            indicator["detail"] = exc.category.value
            result["indicators"].append(indicator)
            continue

        points = [
            {"date": o.observation_date.isoformat(), "value": o.value}
            for o in observations
            if o.value is not None
        ]
        if observations and observations[-1].units:
            indicator["units"] = observations[-1].units
        indicator["recent"] = points[-_RECENT_POINTS:]
        if points:
            latest = points[-1]
            indicator["latest_value"] = latest["value"]
            indicator["latest_date"] = latest["date"]
            if len(points) >= 2:
                prior = points[-2]
                indicator["prior_value"] = prior["value"]
                indicator["prior_date"] = prior["date"]
                indicator["change"] = round(latest["value"] - prior["value"], 6)
            indicator["status"] = "ok"
        else:
            indicator["status"] = "no_data"
        result["indicators"].append(indicator)

    result["retrieved_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    return result


def list_allowlist() -> dict:
    """Document the reviewed allowlist (approved + blocked) with attribution and
    copyright status. Metadata only - no values are fetched."""
    return {
        "panel": PANEL_NAME,
        "notice": REQUIRED_NOTICE,
        "terms_reviewed_url": TERMS_REVIEWED_URL,
        "terms_reviewed_date": TERMS_REVIEWED_DATE,
        "series": [
            {
                "series_id": s.series_id,
                "title": s.title,
                "owner": s.owner,
                "attribution": s.attribution,
                "copyright_status": s.copyright_status.value,
                "retrievable": s.retrievable,
                "purpose": s.purpose,
                "units": s.units,
                "frequency": s.frequency,
                "notes": s.notes or None,
            }
            for s in ALLOWLIST
        ],
    }


def get_series_context(
    settings, series_id: str, as_of: dt.date | None = None, transport=None
) -> dict:
    """Ephemeral observations for a single allowlisted series.

    Enforces the allowlist/copyright gate: raises SeriesNotAllowed / SeriesBlocked
    for anything not approved. Nothing is stored."""
    spec = require_retrievable(series_id)  # raises if unknown/blocked
    if not context_available(settings):
        return {
            **_envelope(as_of),
            "available": False,
            "series_id": spec.series_id,
            "reason": "FRED is disabled or not configured.",
        }
    client = FredClient(settings.fred_api_key, transport=transport)
    start = (as_of or dt.date.today()) - dt.timedelta(days=_LOOKBACK_DAYS)
    observations = client.get_observations(
        spec.series_id, observation_start=start, as_of=as_of
    )
    return {
        **_envelope(as_of),
        "available": True,
        "series_id": spec.series_id,
        "title": spec.title,
        "attribution": spec.attribution,
        "owner": spec.owner,
        "units": (observations[-1].units if observations else spec.units),
        "observations": [
            {"date": o.observation_date.isoformat(), "value": o.value}
            for o in observations
        ],
        "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


__all__ = [
    "PANEL_NAME",
    "REQUIRED_NOTICE",
    "DISCLAIMER",
    "TERMS_REVIEWED_URL",
    "TERMS_REVIEWED_DATE",
    "context_available",
    "build_context",
    "list_allowlist",
    "get_series_context",
    "SeriesBlocked",
    "SeriesNotAllowed",
]
