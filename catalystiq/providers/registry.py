"""The data-source registry: every external source Catalyst IQ integrates,
described as data, plus a single config-driven factory for constructing the
adapters that are actually implemented.

Modeled on catalystiq/validation/reference/registry.py (frozen dataclasses
in a list + lookup helpers) - selection/enablement logic lives here, once,
instead of being scattered across endpoints (spec §1).

Phase 1 reality check: only two adapters are implemented today - Yahoo
Finance (market data) and Webull (brokerage). The registry still lists every
planned source so the health endpoints (§18) and startup validation (§2) can
report the full picture - enabled/disabled, configured/missing - for sources
whose concrete adapter arrives in a later phase. `build_adapter()` raises a
CONFIG-category ProviderError for a source that's registered but not yet
implemented, rather than pretending it exists.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from catalystiq.providers.base import (
    DataDomain,
    LicenseClassification,
    ProviderError,
    ProviderErrorCategory,
)


@dataclass(frozen=True)
class SourceDescriptor:
    """Static description of one external source. Non-secret only - base URLs
    and setting *names*, never values."""

    name: str
    domain: DataDomain
    # Setting attribute names on catalystiq.config.Settings that MUST be
    # non-empty for this source to be usable. Empty for keyless sources.
    required_settings: tuple[str, ...] = ()
    # Setting attribute (bool) that toggles this source. None => always
    # available (keyless, no opt-out), e.g. Yahoo and the NYSE calendar.
    enable_setting: str | None = None
    # Whether the source needs an API key at all (drives .env.example and the
    # health report's "configured/missing" line).
    requires_api_key: bool = False
    license: LicenseClassification = LicenseClassification.UNKNOWN
    base_urls: tuple[str, ...] = ()
    implemented: bool = False
    notes: str = ""
    # Extra config attributes that are optional (surfaced in health, not
    # required for the source to count as configured).
    optional_settings: tuple[str, ...] = field(default_factory=tuple)


# Ordered by domain to mirror the spec's provider tree (§1). base_urls are
# the documented public endpoints; adapters own the exact paths.
SOURCE_REGISTRY: list[SourceDescriptor] = [
    # --- market_data ---
    SourceDescriptor(
        name="yahoo",
        domain=DataDomain.MARKET_DATA,
        enable_setting=None,  # initial primary source, always available
        requires_api_key=False,
        license=LicenseClassification.FREE_PERSONAL,
        base_urls=("https://query1.finance.yahoo.com",),
        implemented=True,
        notes="Initial primary historical market-data provider (via yfinance).",
    ),
    SourceDescriptor(
        name="twelve_data",
        domain=DataDomain.MARKET_DATA,
        required_settings=("twelve_data_api_key",),
        enable_setting="enable_twelve_data",
        requires_api_key=True,
        license=LicenseClassification.FREE_PERSONAL,
        base_urls=("https://api.twelvedata.com",),
        implemented=False,
        notes="Optional secondary/validation source. Disabled by default; free-tier only, not redistributable.",
    ),
    # --- fundamentals ---
    SourceDescriptor(
        name="sec_edgar",
        domain=DataDomain.FUNDAMENTALS,
        required_settings=("sec_user_agent",),
        enable_setting="enable_sec_edgar",
        requires_api_key=False,  # no key, but a descriptive User-Agent is required
        license=LicenseClassification.PUBLIC_DOMAIN,
        base_urls=("https://www.sec.gov", "https://data.sec.gov"),
        implemented=False,
        notes="Requires a descriptive SEC_USER_AGENT per SEC fair-access policy.",
    ),
    # --- macro ---
    SourceDescriptor(
        name="fred",
        domain=DataDomain.MACRO,
        required_settings=("fred_api_key",),
        enable_setting="enable_fred",
        requires_api_key=True,
        license=LicenseClassification.PUBLIC_DOMAIN,
        base_urls=("https://api.stlouisfed.org/fred", "https://api.stlouisfed.org/fred/series/observations"),
        implemented=False,
        notes="FRED + ALFRED (realtime/vintage params) via the same key.",
    ),
    SourceDescriptor(
        name="bls",
        domain=DataDomain.MACRO,
        required_settings=("bls_api_key",),
        enable_setting="enable_bls",
        requires_api_key=True,
        license=LicenseClassification.PUBLIC_DOMAIN,
        base_urls=("https://api.bls.gov/publicAPI/v2",),
        implemented=False,
        notes="Registered key raises daily-request and per-request series limits.",
    ),
    SourceDescriptor(
        name="bea",
        domain=DataDomain.MACRO,
        required_settings=("bea_api_key",),
        enable_setting="enable_bea",
        requires_api_key=True,
        license=LicenseClassification.PUBLIC_DOMAIN,
        base_urls=("https://apps.bea.gov/api/data",),
        implemented=False,
    ),
    # --- calendars ---
    SourceDescriptor(
        name="nyse",
        domain=DataDomain.CALENDARS,
        enable_setting=None,  # authoritative session reference, always available
        requires_api_key=False,
        license=LicenseClassification.FREE_ATTRIBUTION,
        base_urls=("https://www.nyse.com/markets/hours-calendars",),
        implemented=False,
        notes="Operationally backed by pandas_market_calendars (already a dependency), validated against the official NYSE schedule.",
    ),
    # --- regulatory ---
    SourceDescriptor(
        name="finra",
        domain=DataDomain.REGULATORY,
        enable_setting="enable_finra",
        requires_api_key=False,
        license=LicenseClassification.FREE_ATTRIBUTION,
        base_urls=("https://api.finra.org", "https://cdn.finra.org"),
        implemented=False,
        notes="Equity short interest and daily short-sale volume kept as separate datasets.",
    ),
    SourceDescriptor(
        name="nasdaq_trader",
        domain=DataDomain.REGULATORY,
        enable_setting="enable_nasdaq_trader",
        requires_api_key=False,
        license=LicenseClassification.FREE_ATTRIBUTION,
        base_urls=("https://www.nasdaqtrader.com/dynamic/SymDir", "http://www.nasdaqtrader.com"),
        implemented=False,
        notes="Symbol directory + reference datasets. Normalize to stable internal security ids, not raw tickers.",
    ),
    # --- brokerage ---
    SourceDescriptor(
        name="webull",
        domain=DataDomain.BROKERAGE,
        required_settings=("webull_app_key", "webull_app_secret", "webull_account_id"),
        enable_setting="enable_webull",
        requires_api_key=True,
        license=LicenseClassification.PROPRIETARY,
        base_urls=("https://developer.webull.com",),
        implemented=True,
        notes="Read-only broker/order-verification. Order submission stays disabled until a separately approved phase.",
    ),
]

_BY_NAME = {s.name: s for s in SOURCE_REGISTRY}


def get_source(name: str) -> SourceDescriptor | None:
    return _BY_NAME.get(name)


def sources_for_domain(domain: DataDomain) -> list[SourceDescriptor]:
    return [s for s in SOURCE_REGISTRY if s.domain == domain]


def is_source_enabled(name: str, settings) -> bool:
    """Whether a source is turned on. A source with no `enable_setting` is
    always on; otherwise the named bool setting decides."""
    source = get_source(name)
    if source is None:
        return False
    if source.enable_setting is None:
        return True
    return bool(getattr(settings, source.enable_setting, False))


def missing_settings(name: str, settings) -> list[str]:
    """Required setting names that are empty/unset for this source. Returns
    the setting *names* only - never their values (secrets)."""
    source = get_source(name)
    if source is None:
        return []
    return [attr for attr in source.required_settings if not getattr(settings, attr, "")]


def is_source_configured(name: str, settings) -> bool:
    return not missing_settings(name, settings)


def build_adapter(name: str, settings=None):
    """Construct the concrete adapter for `name`, honoring enable flags and
    required config. Raises ProviderError(category=CONFIG) - never a bare
    KeyError or a silent None - for an unknown, disabled, unimplemented, or
    unconfigured source, so callers get one predictable failure type.

    Only the implemented adapters (yahoo, webull) can actually be built
    today; every other registered source raises "not implemented until a
    later phase" rather than being faked."""
    from catalystiq.config import get_settings

    settings = settings or get_settings()
    source = get_source(name)
    if source is None:
        raise ProviderError(
            f"Unknown data source {name!r}.", category=ProviderErrorCategory.CONFIG, provider=name
        )
    if not is_source_enabled(name, settings):
        raise ProviderError(
            f"Data source {name!r} is disabled (set {source.enable_setting} to enable it).",
            category=ProviderErrorCategory.CONFIG,
            provider=name,
        )
    missing = missing_settings(name, settings)
    if missing:
        raise ProviderError(
            f"Data source {name!r} is missing required configuration: {', '.join(missing)}.",
            category=ProviderErrorCategory.CONFIG,
            provider=name,
        )
    if not source.implemented:
        raise ProviderError(
            f"Data source {name!r} is registered but its adapter is not implemented yet.",
            category=ProviderErrorCategory.CONFIG,
            provider=name,
        )

    if name == "yahoo":
        from catalystiq.providers.market_data import YahooFinanceProvider

        return YahooFinanceProvider()
    if name == "webull":
        from catalystiq.providers.broker import get_broker_provider

        return get_broker_provider()

    # Unreachable: every implemented source is handled above. Guard anyway so
    # marking a source implemented without wiring it here fails loudly.
    raise ProviderError(
        f"Data source {name!r} is marked implemented but has no factory branch.",
        category=ProviderErrorCategory.CONFIG,
        provider=name,
    )
