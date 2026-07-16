"""Centralized settings, loaded from environment variables / .env."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Auth for the paper-trading action endpoints.
    action_api_key: str = ""

    # Broker (paper trading) credentials.
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
