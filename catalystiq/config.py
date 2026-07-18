"""Centralized settings, loaded from environment variables / .env."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Auth for the paper-trading action endpoints.
    action_api_key: str = ""

    # Which BrokerProvider to use. Webull is the only supported, active
    # broker - see catalystiq/providers/broker.py's get_broker_provider(),
    # which rejects any other value rather than falling back to anything
    # else.
    broker_provider: str = "webull"

    # Webull OpenAPI credentials (https://developer.webull.com/apis/docs/trade-api/getting-started).
    # region_id is e.g. "us" or "hk"; api_endpoint defaults to Webull's own
    # resolver but can be pointed at the sandbox host explicitly.
    webull_app_key: str = ""
    webull_app_secret: str = ""
    webull_account_id: str = ""
    webull_region_id: str = "us"
    webull_api_endpoint: str = ""
    webull_token_dir: str = ""

    # Market data provider (legacy single-provider knob, kept for the
    # existing get_market_data_provider() factory). The primary/secondary
    # settings below are the forward-looking source-priority controls (§16).
    market_data_provider: str = "yahoo"

    # --- Data-source integration (spec §2) ---------------------------
    # Source priority for market data. Yahoo stays the initial primary
    # historical source; Twelve Data is the optional secondary/validation
    # source and is off unless explicitly enabled with a key.
    market_data_primary_provider: str = "yahoo"
    market_data_secondary_provider: str = "twelve_data"

    # Per-source enable flags. A source with no API key (Yahoo, NYSE) has no
    # flag - it's always available; these gate the rest.
    #
    # Secure-by-default: sources that need a key or required config
    # (sec_edgar/fred/bls/bea, twelve_data, webull) default OFF, so a fresh
    # clone boots without credentials. Turn one on deliberately and its
    # config becomes required - validate_settings() then fails fast if it's
    # missing. Keyless sources (finra, nasdaq_trader) default ON.
    enable_twelve_data: bool = False
    enable_sec_edgar: bool = False
    enable_fred: bool = False
    enable_bls: bool = False
    enable_bea: bool = False
    enable_finra: bool = True
    enable_nasdaq_trader: bool = True
    enable_webull: bool = False

    # Provider API keys / credentials. Empty by default; only required when
    # the owning source is enabled (see validate_settings()). Never commit
    # real values - see .env.example.
    fred_api_key: str = ""
    bls_api_key: str = ""
    bea_api_key: str = ""
    twelve_data_api_key: str = ""

    # SEC EDGAR requires a descriptive User-Agent (contact info) per its
    # fair-access policy - it's not a secret, but the source is unusable
    # without it, so it's treated as required config when SEC is enabled.
    sec_user_agent: str = ""

    # Optional cap on Twelve Data requests per day so the free-tier
    # allowance isn't consumed in one run (§5). 0 => no local cap.
    twelve_data_daily_request_budget: int = 0

    # Storage. Defaults to a local SQLite file so the app runs without
    # infrastructure in dev; point DATABASE_URL at Postgres in production
    # per the target architecture (§1.1 / §7 of the build spec).
    database_url: str = "sqlite:///./catalystiq.db"

    # Data Validation Layer thresholds (§2.9).
    price_gap_zscore_threshold: float = 3.0
    price_history_lookback_years: int = 5

    # Local dev: origin(s) allowed to call the API directly from a browser
    # (the Vite dev server). Comma-separated.
    cors_allow_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Reference-calculation adapter (catalystiq/validation/reference/):
    # fraction of recently-succeeded Gold calculation runs the async
    # validation loop samples for reference checking each cycle, on top of
    # every run the synchronous anomaly check has already flagged.
    reference_validation_sample_rate: float = 0.05
    reference_validation_interval_seconds: int = 300


class ConfigurationError(RuntimeError):
    """Raised at startup when an enabled data source is missing required
    configuration. The message lists offending setting *names* only - never
    their values - so a misconfiguration surfaces clearly without leaking a
    secret into logs or an API response."""


def validate_settings(settings: "Settings | None" = None) -> None:
    """Fail fast if an *enabled* source is missing required configuration.

    A disabled or keyless source never blocks startup - missing optional
    keys for providers you aren't using are fine (spec acceptance §6). Only
    setting names are reported; values are never read into the message.
    """
    # Imported lazily so this module has no import-time dependency on the
    # providers package (registry imports from providers.base only).
    from catalystiq.providers.registry import (
        SOURCE_REGISTRY,
        get_source,
        is_source_enabled,
        missing_settings,
    )

    settings = settings or get_settings()
    problems: list[str] = []

    for source in SOURCE_REGISTRY:
        if not is_source_enabled(source.name, settings):
            continue
        # Phase 1 gate: only enforce config for sources whose adapter is
        # actually wired. Enabling a not-yet-implemented source (SEC/FRED/
        # etc.) is an intent declaration that must not hard-fail startup and
        # take the working app (Yahoo) down with it - its keys become
        # required once its adapter lands in a later phase.
        if not source.implemented:
            continue
        missing = missing_settings(source.name, settings)
        if missing:
            problems.append(
                f"source '{source.name}' is enabled but missing required setting(s): "
                f"{', '.join(missing)}"
            )

    # Primary/secondary market-data providers must name real market_data
    # sources; a secondary that's named but disabled is fine (it just won't
    # be used), but a typo'd/unknown name is a config error.
    for role, name in (
        ("MARKET_DATA_PRIMARY_PROVIDER", settings.market_data_primary_provider),
        ("MARKET_DATA_SECONDARY_PROVIDER", settings.market_data_secondary_provider),
    ):
        source = get_source(name)
        if source is None:
            problems.append(f"{role}={name!r} is not a known data source")

    if problems:
        raise ConfigurationError(
            "Invalid data-source configuration:\n  - " + "\n  - ".join(problems)
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
