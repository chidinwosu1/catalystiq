"""Manually-controlled FRED series allowlist (compliance requirement #5).

Only series that appear here AND are classified PUBLIC_DOMAIN may be
retrieved. Anything else - an unknown id, or a series marked
COPYRIGHTED_PREAPPROVAL - is hard-blocked, because arbitrary retrieval is not
permitted until a series' ownership and copyright status have been reviewed.

Editing this list is a deliberate, reviewed act: each entry records the
original data owner, the required attribution string, the copyright
classification, the purpose it serves, and its units/frequency. Adding a
series (or promoting one out of "blocked") requires a fresh terms review and
the owner's explicit approval (requirement #10). See FRED_COMPLIANCE.md.

Nothing in this module touches a database, cache, or log - it is pure data.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CopyrightStatus(str, Enum):
    """Copyright classification governing whether a series may be retrieved."""

    # Public-domain U.S. federal-government data: freely usable with attribution.
    PUBLIC_DOMAIN = "public_domain"
    # FRED marks some third-party series "Copyrighted: Pre-approval required."
    # These are HARD-BLOCKED here until separately reviewed and approved.
    COPYRIGHTED_PREAPPROVAL = "copyrighted_preapproval"


class SeriesNotAllowed(Exception):
    """Raised when a series id is not on the allowlist at all."""


class SeriesBlocked(Exception):
    """Raised when a listed series is blocked (copyrighted / pre-approval)."""


@dataclass(frozen=True)
class FredSeriesSpec:
    """Static, reviewed description of one FRED series. Non-secret data only."""

    series_id: str
    title: str
    # The ORIGINAL data owner (not "FRED"): the agency/entity that produced it.
    owner: str
    # The attribution string that MUST be displayed beside the indicator.
    attribution: str
    copyright_status: CopyrightStatus
    # Why this indicator is included (permitted-purpose documentation, req #9).
    purpose: str
    units: str
    frequency: str
    notes: str = ""

    @property
    def retrievable(self) -> bool:
        """Only clearly public-domain series may be fetched and displayed."""
        return self.copyright_status is CopyrightStatus.PUBLIC_DOMAIN


# The reviewed allowlist. Approved entries are public-domain U.S. federal data;
# the two COPYRIGHTED_PREAPPROVAL entries are kept as explicit, documented
# blocks so the enforcement is visible and tested - they are NEVER retrieved.
ALLOWLIST: tuple[FredSeriesSpec, ...] = (
    FredSeriesSpec(
        series_id="DGS10",
        title="10-Year Treasury Constant Maturity Rate",
        owner="Board of Governors of the Federal Reserve System (US)",
        attribution="Source: Board of Governors of the Federal Reserve System (US) via FRED",
        copyright_status=CopyrightStatus.PUBLIC_DOMAIN,
        purpose="Long-term risk-free rate as macro backdrop.",
        units="Percent",
        frequency="Daily",
    ),
    FredSeriesSpec(
        series_id="DGS2",
        title="2-Year Treasury Constant Maturity Rate",
        owner="Board of Governors of the Federal Reserve System (US)",
        attribution="Source: Board of Governors of the Federal Reserve System (US) via FRED",
        copyright_status=CopyrightStatus.PUBLIC_DOMAIN,
        purpose="Short-end rate; pairs with DGS10 for curve context.",
        units="Percent",
        frequency="Daily",
    ),
    FredSeriesSpec(
        series_id="T10Y2Y",
        title="10-Year minus 2-Year Treasury Constant Maturity",
        owner="Federal Reserve Bank of St. Louis",
        attribution="Source: Federal Reserve Bank of St. Louis via FRED",
        copyright_status=CopyrightStatus.PUBLIC_DOMAIN,
        purpose="Yield-curve slope; a widely watched recession-signal spread.",
        units="Percent",
        frequency="Daily",
    ),
    FredSeriesSpec(
        series_id="FEDFUNDS",
        title="Effective Federal Funds Rate",
        owner="Board of Governors of the Federal Reserve System (US)",
        attribution="Source: Board of Governors of the Federal Reserve System (US) via FRED",
        copyright_status=CopyrightStatus.PUBLIC_DOMAIN,
        purpose="Policy-rate backdrop.",
        units="Percent",
        frequency="Monthly",
    ),
    FredSeriesSpec(
        series_id="UNRATE",
        title="Unemployment Rate",
        owner="U.S. Bureau of Labor Statistics",
        attribution="Source: U.S. Bureau of Labor Statistics via FRED",
        copyright_status=CopyrightStatus.PUBLIC_DOMAIN,
        purpose="Labor-market backdrop.",
        units="Percent",
        frequency="Monthly",
    ),
    FredSeriesSpec(
        series_id="CPIAUCSL",
        title="Consumer Price Index for All Urban Consumers: All Items (SA)",
        owner="U.S. Bureau of Labor Statistics",
        attribution="Source: U.S. Bureau of Labor Statistics via FRED",
        copyright_status=CopyrightStatus.PUBLIC_DOMAIN,
        purpose="Headline inflation backdrop.",
        units="Index 1982-1984=100",
        frequency="Monthly",
    ),
    FredSeriesSpec(
        series_id="GDPC1",
        title="Real Gross Domestic Product",
        owner="U.S. Bureau of Economic Analysis",
        attribution="Source: U.S. Bureau of Economic Analysis via FRED",
        copyright_status=CopyrightStatus.PUBLIC_DOMAIN,
        purpose="Aggregate growth backdrop.",
        units="Billions of Chained 2017 Dollars",
        frequency="Quarterly",
    ),
    # --- Documented HARD-BLOCKS (copyrighted; pre-approval required) --------
    # Present so the block is explicit, reviewable, and covered by tests.
    # These are NEVER fetched or displayed.
    FredSeriesSpec(
        series_id="VIXCLS",
        title="CBOE Volatility Index: VIX",
        owner="Chicago Board Options Exchange (CBOE)",
        attribution="Source: Chicago Board Options Exchange (CBOE) via FRED",
        copyright_status=CopyrightStatus.COPYRIGHTED_PREAPPROVAL,
        purpose="BLOCKED example - copyrighted third-party series.",
        units="Index",
        frequency="Daily",
        notes="Copyrighted: pre-approval required. Hard-blocked until reviewed.",
    ),
    FredSeriesSpec(
        series_id="SP500",
        title="S&P 500",
        owner="S&P Dow Jones Indices LLC",
        attribution="Source: S&P Dow Jones Indices LLC via FRED",
        copyright_status=CopyrightStatus.COPYRIGHTED_PREAPPROVAL,
        purpose="BLOCKED example - copyrighted third-party index.",
        units="Index",
        frequency="Daily",
        notes="Copyrighted: pre-approval required. Hard-blocked until reviewed.",
    ),
)

_BY_ID: dict[str, FredSeriesSpec] = {s.series_id: s for s in ALLOWLIST}


def get_spec(series_id: str) -> FredSeriesSpec | None:
    """Return the spec for a series id (case-insensitive), or None if unlisted."""
    if not series_id:
        return None
    return _BY_ID.get(series_id.strip().upper())


def approved_series() -> list[FredSeriesSpec]:
    """The retrievable (public-domain) series, in listed order."""
    return [s for s in ALLOWLIST if s.retrievable]


def require_retrievable(series_id: str) -> FredSeriesSpec:
    """Return the spec only if the series is listed AND retrievable.

    Raises SeriesNotAllowed for an unknown id and SeriesBlocked for a listed
    but copyrighted/pre-approval series. Callers must never fetch a series
    without passing through this gate."""
    spec = get_spec(series_id)
    if spec is None:
        raise SeriesNotAllowed(
            f"FRED series {series_id!r} is not on the reviewed allowlist."
        )
    if not spec.retrievable:
        raise SeriesBlocked(
            f"FRED series {series_id!r} is blocked "
            f"({spec.copyright_status.value}); retrieval requires pre-approval."
        )
    return spec
