"""Centralized settings, loaded from environment variables / .env."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Deployment environment. "production" enforces the auth-hardening rules
    # in validate_auth_config() (explicit, distinct, secure secrets - no
    # fallbacks). Anything else (development/test) allows the dev
    # conveniences below. Set ENVIRONMENT=production for real deployments.
    environment: str = "development"

    # Auth for the action endpoints. `action_api_key` is the programmatic
    # bearer token (server-to-server, CI, cron). Browsers use a session
    # cookie instead (see catalystiq/routers/auth.py) so the raw key never
    # reaches the browser bundle.
    action_api_key: str = ""

    # Session-cookie auth.
    #   app_password  - the private password a user types to log into
    #                   Catalyst IQ (verified at /auth/login).
    #   session_secret - a SEPARATE secret used ONLY to sign session cookies;
    #                   it is never the login password and never leaves the
    #                   server.
    # In development/test these fall back to action_api_key for convenience.
    # In production there is NO fallback and NO default: both must be set
    # explicitly, be distinct from each other and from action_api_key, meet a
    # minimum length, and the cookie must be Secure - validate_auth_config()
    # fails startup otherwise.
    app_password: str = ""
    session_secret: str = ""
    session_ttl_seconds: int = 60 * 60 * 12  # 12 hours
    session_cookie_name: str = "ciq_session"
    # Secure=True means the cookie is only sent over HTTPS. Required in
    # production; set False only for local HTTP development.
    session_cookie_secure: bool = True
    session_cookie_samesite: str = "lax"

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"

    @property
    def effective_session_secret(self) -> str:
        # No fallback in production - validate_auth_config() guarantees
        # session_secret is set there, so the cookie key is never the action
        # key or the login password.
        if self.is_production:
            return self.session_secret
        return self.session_secret or self.action_api_key

    @property
    def effective_app_password(self) -> str:
        if self.is_production:
            return self.app_password
        return self.app_password or self.action_api_key

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

    # --- Order submission gating (§13) -------------------------------
    # Order submission is DISABLED by default. Paper and live are separate
    # flags with separate credentials; live stays unavailable until
    # separately approved (assert_submission_allowed in the broker router
    # refuses live even if its flag is set).
    trading_mode: str = "paper"  # paper | live
    enable_paper_order_submission: bool = False
    enable_live_order_submission: bool = False
    # Secret for the per-order confirmation HMAC. Submission stays refused
    # until this is set (no secret => no valid tokens can be minted).
    order_confirmation_secret: str = ""
    order_confirmation_ttl_seconds: int = 300
    # Separate live-trading credentials (unused until live is approved).
    webull_live_app_key: str = ""
    webull_live_app_secret: str = ""
    webull_live_account_id: str = ""

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

    # Twelve Data plan credit limits, enforced CENTRALLY through the shared
    # credit gate (catalystiq/providers/twelve_data_gate.py). Defaults are the
    # Basic plan: 8 credits/minute, 800 credits/day. Requests are measured in
    # credits (some endpoints cost more than one). See TWELVE_DATA_COMPLIANCE.md.
    twelve_data_credits_per_minute: int = 8
    twelve_data_credits_per_day: int = 800
    # Optional local cap that can only LOWER the daily credit budget (never
    # raise it). 0 => use the plan's per-day limit above.
    twelve_data_daily_request_budget: int = 0

    # Optional comma-separated override of the BLS series ids to track
    # (§8: configured, not hardcoded). Empty => use providers/bls.py's
    # DEFAULT_BLS_SERIES.
    bls_series_ids: str = ""

    # Optional comma-separated override of the BEA dataset:table:frequency
    # tuples to track (§9). Empty => providers/bea.py's DEFAULT_BEA_TABLES.
    bea_tables: str = ""

    # Cross-provider price comparison tolerance (§16): a relative difference
    # above this (percent) between the primary and secondary provider raises
    # a data-quality warning. Values are never averaged.
    provider_comparison_tolerance_pct: float = 0.5

    # Yahoo-outage fallback to the secondary market-data provider - only used
    # when explicitly enabled (§5). Off by default; the primary is never
    # silently replaced.
    market_data_fallback_enabled: bool = False

    # --- Fundamentals fetch governance -------------------------------------
    # Fundamentals (Yahoo `.info`) are slow-changing and the endpoint is
    # aggressively per-IP rate limited, so every fundamentals fetch goes
    # through a governed cache (catalystiq/providers/fundamentals_cache.py):
    # a long TTL cache, single-flight de-duplication of identical in-flight
    # calls, a per-provider concurrency limit, and a circuit-breaker cooldown
    # that fails fast after repeated 429s instead of hammering a throttled
    # endpoint. All four are configurable; the defaults are conservative.
    fundamentals_cache_ttl_seconds: int = 6 * 60 * 60  # 6 hours
    fundamentals_max_concurrency: int = 2
    # Consecutive rate-limited (429) fetches that trip the cooldown, and how
    # long the cooldown lasts (fail-fast, no provider calls) before a single
    # trial is allowed again.
    fundamentals_rate_limit_threshold: int = 3
    fundamentals_rate_limit_cooldown_seconds: int = 300  # 5 minutes

    # --- Market-data (OHLCV/quote) fetch governance ------------------------
    # The OHLCV/quote ingest path (yfinance .history/.fast_info) is on the
    # same throttled Yahoo endpoints and, on a cold cache, an opportunity scan
    # ingests 5y of history for ~30 symbols sequentially. These gate the
    # provider ingest calls (concurrency cap + a rate-limit circuit-breaker
    # cooldown) so throttling degrades fast instead of hanging.
    market_data_max_concurrency: int = 2
    market_data_rate_limit_threshold: int = 3
    market_data_rate_limit_cooldown_seconds: int = 300  # 5 minutes

    # --- Opportunity-scan performance --------------------------------------
    # Scoring is CPU-bound: each symbol computes five analytical snapshots, and
    # the score's longest indicator lookback is ~200 sessions, so feeding it 5y
    # of bars is ~3x wasted work. Cap the bars used for SCORING only (ingestion
    # and other analyses still keep full history); verified score-identical vs
    # full history in tests.
    scoring_max_bars: int = 300

    # The ranked universe scan is served from a cache that the background warmer
    # refreshes each pass, so the user request is a pure cache read instead of a
    # ~tens-of-seconds scoring loop. The TTL is the fallback staleness bound if
    # the warmer stalls; it is intentionally longer than the warm interval so a
    # warmed entry is always served between passes.
    opportunity_scan_cache_ttl_seconds: int = 1800  # 30 min (fallback bound)

    # Background universe warmer: keeps Silver fresh for the scan universe
    # (+ SPY + governed sector ETFs) so the user-facing scan is warm (DB-only,
    # sub-second) instead of paying a cold multi-fetch ingest. On by default;
    # the interval is calendar-aware via FreshnessPolicy (a warm symbol is a
    # no-op), so an idle app makes at most a light periodic refresh.
    enable_universe_warmer: bool = True
    universe_warm_interval_seconds: int = 900  # 15 minutes

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

    # --- Machine-learning foundation (three-model + ranker system) --------
    # The entire ML subsystem is OFF by default and every gate below FAILS
    # CLOSED: a missing, invalid or falsey flag means "not permitted", never
    # "assume enabled". No user-facing prediction is ever served unless
    # ENABLE_ML *and* the specific stage flag are true AND an approved model
    # artifact exists for the requested family/direction/horizon
    # (see catalystiq/ml/flags.py, which is the single decision point).
    #
    # `enable_ml` is the master switch - if it is false, training, inference
    # and ranking are all refused regardless of their individual flags.
    enable_ml: bool = False
    # Offline pipeline: building datasets, fitting and evaluating candidate
    # artifacts. Never touches a user request path.
    enable_ml_training: bool = False
    # Online pipeline: assembling the unified inference contract for a
    # symbol. Even when true, it returns `not_available` unless approved
    # artifacts exist (see ml_require_approved_models).
    enable_ml_inference: bool = False
    # Model 4 cross-sectional opportunity ranking (replaces the hard-coded
    # opportunity list). Requires approved Model 1-3 artifacts as well.
    enable_ml_ranking: bool = False

    # Hard requirement that only registry artifacts with
    # approval_status='approved' may serve user-facing predictions. This is
    # a safety rail and stays TRUE by default; setting it false is only for
    # controlled offline experiments and is itself gated by enable_ml.
    ml_require_approved_models: bool = True
    ml_ranker_require_approved_model: bool = True

    # Licensing gates for feature sourcing (compliance). FRED-derived values
    # are REJECTED from ML features outright (see FRED_COMPLIANCE.md); this
    # flag exists only so the rejection is explicit and testable and cannot
    # be flipped on by accident - it defaults false and the feature schema
    # blocks FRED regardless (defense in depth). Twelve Data may not enter
    # training unless a separate licensing flag confirms storage + ML use
    # are permitted (see TWELVE_DATA_COMPLIANCE.md).
    ml_allow_fred_features: bool = False
    ml_allow_twelve_data_training: bool = False

    # Model 4 display controls. At most this many names may appear in the
    # "Highest Conviction" section; the broader opportunity table cap is
    # separate and larger. Both are configurable, never hard-coded in the UI.
    ml_ranker_max_highest_conviction: int = 4
    ml_ranker_max_opportunity_table: int = 25
    # Demo/synthetic data may back UNIT TESTS only, never a user-facing
    # artifact - this stays false and inference refuses to serve any artifact
    # whose training_data_version is marked synthetic.
    ml_ranker_allow_demo_data: bool = False

    # --- Model 5: Aggregate Investor Functional Response ------------------
    # Separate, independently-gated family backing the Investor Functional
    # Behavior Analysis section. It analyzes AGGREGATE market behavior only
    # (never an individual investor's psychology) and never alters Models
    # 1-4 outputs. All flags default false and fail closed, mirroring the
    # core ML gates above. This family is enabled only when ENABLE_ML is also
    # true (see catalystiq/ml/flags.py behavior_* helpers).
    enable_aggregate_behavior_model: bool = False
    enable_behavior_model_training: bool = False
    enable_behavior_model_inference: bool = False
    behavior_model_allow_fred: bool = False
    behavior_model_allow_twelve_data_training: bool = False
    behavior_model_require_approved_artifact: bool = True
    behavior_model_allow_demo_data: bool = False

    # --- MLflow experiment tracking (offline training/validation only) -----
    # Where the offline training/validation runner (catalystiq/ml/train_cli.py)
    # records parameters, metrics and artifacts. These configure tracking ONLY;
    # they never enable training, inference, serving or approval - those stay
    # gated by the fail-closed flags above. Credentials and remote URLs are
    # NEVER hard-coded: leave the tracking URI blank and a local ``mlruns``
    # directory is used for development. Point it at a server via the
    # MLFLOW_TRACKING_URI environment variable (which MLflow also reads
    # natively) when you want a shared backend; any auth is supplied through
    # MLflow's own environment variables, not this file.
    mlflow_tracking_uri: str = ""
    mlflow_experiment_name: str = "catalystiq-ml-validation"
    # Optional local directory used when no tracking URI is configured. A
    # relative path is resolved against the current working directory at run
    # time, so `mlflow ui` started from the repo root finds it.
    mlflow_local_dir: str = "mlruns"


class ConfigurationError(RuntimeError):
    """Raised at startup when an enabled data source is missing required
    configuration. The message lists offending setting *names* only - never
    their values - so a misconfiguration surfaces clearly without leaking a
    secret into logs or an API response."""


# Minimum strengths for the production auth secrets. The session secret is a
# signing key (should be long/random); the password is human-entered.
MIN_APP_PASSWORD_LENGTH = 12
MIN_SESSION_SECRET_LENGTH = 32


def validate_auth_config(settings: "Settings") -> list[str]:
    """Production auth-hardening checks (§ requirements 4/5). Returns a list of
    problems (setting NAMES only, never values). Empty in non-production - the
    dev/test fallback to action_api_key is allowed there.

    In production: APP_PASSWORD and SESSION_SECRET must both be set (no
    fallback, no default), non-blank, of minimum length, distinct from each
    other and from ACTION_API_KEY, and the session cookie must be Secure.
    """
    if not settings.is_production:
        return []

    problems: list[str] = []
    pw = settings.app_password.strip()
    secret = settings.session_secret.strip()
    api_key = settings.action_api_key.strip()

    if not pw:
        problems.append("APP_PASSWORD must be set explicitly in production (no default/fallback).")
    else:
        if len(pw) < MIN_APP_PASSWORD_LENGTH:
            problems.append(f"APP_PASSWORD must be at least {MIN_APP_PASSWORD_LENGTH} characters.")
        if api_key and pw == api_key:
            problems.append("APP_PASSWORD must not fall back to / equal ACTION_API_KEY.")

    if not secret:
        problems.append("SESSION_SECRET must be set explicitly in production (no default/fallback).")
    else:
        if len(secret) < MIN_SESSION_SECRET_LENGTH:
            problems.append(
                f"SESSION_SECRET must be at least {MIN_SESSION_SECRET_LENGTH} characters."
            )
        if api_key and secret == api_key:
            problems.append("SESSION_SECRET must not fall back to / equal ACTION_API_KEY.")

    if pw and secret and pw == secret:
        problems.append("APP_PASSWORD and SESSION_SECRET must be different values.")

    if not settings.session_cookie_secure:
        problems.append("SESSION_COOKIE_SECURE must be true in production.")

    return problems


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

    # Auth hardening first (production fail-fast; §4/§5). Names only.
    problems.extend(validate_auth_config(settings))

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
            "Invalid configuration:\n  - " + "\n  - ".join(problems)
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
