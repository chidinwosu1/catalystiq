"""Contracts for the market-data diagnostics endpoint - a one-call health check
that explains WHY the Trade Center may be empty (provider reachable? rate-
limited? how many symbols scored?) instead of guessing from a screenshot."""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class ProviderProbe(BaseModel):
    """Result of a single live probe against a market-data provider."""

    provider: str
    symbol: str
    ok: bool
    rate_limited: bool  # the failure looked like an upstream 429 / throttle
    detail: str
    latency_ms: int


class MarketDataDiagnostics(BaseModel):
    checked_at: dt.datetime
    config: dict  # which providers are configured (no secrets)
    daily_provider_probe: ProviderProbe  # the Setup Strength / scan source
    intraday_provider_probe: ProviderProbe  # the Entry Check source
    gate_stats: dict  # per-provider MarketDataGate counters (rate-limit cooldowns)
    scan_cache: dict  # cached-scan / background-warm state
    summary: str  # human-readable one-liner diagnosis
