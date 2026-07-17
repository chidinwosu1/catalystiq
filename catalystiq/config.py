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

    # Market data provider.
    market_data_provider: str = "yahoo"

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
