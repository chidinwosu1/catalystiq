"""Shared provider-adapter vocabulary: data domains, provider identity, data
classification, ingestion statuses, and typed provider errors.

This is Phase 1 foundation (see README's data-source roadmap). It does NOT
move the two existing, working adapters
(catalystiq/providers/market_data.py's YahooFinanceProvider,
catalystiq/providers/broker.py's WebullBroker) - it only gives every
adapter, existing and future, one place to declare *what it is* (name,
version, domain, whether its data is real-time/delayed/end-of-day/revised)
and one shared error/category vocabulary, so downstream code and the Bronze
ingestion-run record can reason about any provider uniformly instead of
per-provider special-casing.

Nothing here performs I/O, retries, or rate limiting - that lives in
catalystiq/providers/transport.py. Nothing here computes indicators or
scores - adapters return normalized provider-domain objects only.
"""
from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable


class DataDomain(str, Enum):
    """The kind of data an adapter produces. One adapter serves exactly one
    domain; the registry (catalystiq/providers/registry.py) groups adapters
    by this."""

    MARKET_DATA = "market_data"
    FUNDAMENTALS = "fundamentals"
    MACRO = "macro"
    CALENDARS = "calendars"
    REGULATORY = "regulatory"
    BROKERAGE = "brokerage"
    NEWS = "news"


class DataClassification(str, Enum):
    """How "final" a returned datum is. Persisted on Bronze ingestion runs so
    a downstream consumer never mistakes a delayed/incomplete value for a
    settled one, and so a revised macro observation is never conflated with
    the vintage that was actually known at an earlier point in time."""

    REAL_TIME = "real_time"
    DELAYED = "delayed"
    END_OF_DAY = "end_of_day"
    REVISED = "revised"
    REFERENCE = "reference"  # slow-changing reference data (symbol dirs, calendars)
    UNKNOWN = "unknown"


class LicenseClassification(str, Enum):
    """Usage/redistribution class of a source's data. Recorded on ingestion
    runs and in the source registry so a free individual-plan source is
    never silently treated as commercially redistributable (spec §5, §12)."""

    PUBLIC_DOMAIN = "public_domain"          # US-government works (SEC, BLS, BEA, FRED source data)
    FREE_ATTRIBUTION = "free_attribution"    # free but attribution/terms apply
    FREE_PERSONAL = "free_personal"          # free individual plan, NOT redistributable (Twelve Data free)
    PROPRIETARY = "proprietary"              # broker/account data, not market redistribution
    UNKNOWN = "unknown"


class IngestionStatus(str, Enum):
    """Terminal (and one non-terminal) states of a Bronze ingestion run,
    matching the spec's allowed set (§3). The price-bar pipeline today only
    ever lands on running/succeeded/partial/failed; rate_limited/unavailable
    exist for the network-backed adapters added in later phases."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"


class ProviderErrorCategory(str, Enum):
    """Normalized failure taxonomy across every provider, so the ingestion
    record's `error_category` is comparable regardless of which underlying
    library/HTTP client raised. Sanitized message goes alongside; secrets
    never enter either."""

    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"        # 5xx / provider down / circuit open
    AUTH = "auth"                      # 401/403 / bad or missing credentials
    NOT_FOUND = "not_found"            # 404 / unknown symbol|series|CIK
    MALFORMED_RESPONSE = "malformed_response"
    NETWORK = "network"               # DNS/connection reset, non-HTTP transport failure
    CONFIG = "config"                 # provider disabled or missing required setting
    UNKNOWN = "unknown"


class ProviderError(RuntimeError):
    """Base error every adapter raises on failure, tagged with a normalized
    category. Adapters translate provider-/library-specific failures into
    this so callers and the ingestion record stay provider-agnostic.

    `MarketDataError` (providers/market_data.py) and `BrokerError`
    (providers/broker.py) predate this and are intentionally left as-is to
    preserve their existing catch sites; new domain adapters raise
    `ProviderError` (or a subclass) directly."""

    def __init__(
        self,
        message: str,
        *,
        category: ProviderErrorCategory = ProviderErrorCategory.UNKNOWN,
        provider: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.provider = provider
        self.status_code = status_code


@runtime_checkable
class ProviderAdapter(Protocol):
    """Structural contract every adapter satisfies: it can identify itself by
    provider name + adapter version and declare its data domain. Deliberately
    minimal and structural (a Protocol, not an ABC) so the two existing
    adapters conform just by carrying the three class attributes below - no
    inheritance change, no touch to their method signatures.

    - PROVIDER_NAME: stable, lowercase source key (e.g. "yahoo", "sec_edgar",
      "fred"); also the registry key and what's written to a Bronze run's
      `provider` field going forward.
    - ADAPTER_VERSION: bumped whenever parsing/field-mapping changes, so a
      Gold result traces to the exact adapter build that produced its source
      data.
    - DOMAIN: the single DataDomain this adapter serves.
    """

    PROVIDER_NAME: str
    ADAPTER_VERSION: str
    DOMAIN: DataDomain
