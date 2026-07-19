"""The point-in-time provenance contract: enums, model, dynamic freshness,
temporal-ordering validation, and the ML lookahead guard.

All timestamps are treated as timezone-aware UTC; naive datetimes coming from
storage (SQLite has no tz) are assumed UTC.
"""
from __future__ import annotations

import datetime as dt
from enum import Enum

from pydantic import BaseModel


class DataQualityStatus(str, Enum):
    VALID = "valid"
    WARNING = "warning"
    INVALID = "invalid"
    INSUFFICIENT = "insufficient"
    QUARANTINED = "quarantined"


class Freshness(str, Enum):
    CURRENT = "current"
    STALE = "stale"
    FUTURE_DATED = "future_dated"
    UNKNOWN = "unknown"


class LookaheadViolation(Exception):
    """Raised when a feature's data would not have been known at prediction time."""


# --- Canonical provider ids --------------------------------------------

_CANONICAL_PROVIDERS = {
    "yahoo", "sec_edgar", "bls", "bea", "fred_restricted", "finra",
    "nasdaq_trader", "twelve_data", "webull", "nyse",
}
# Map legacy / class-name forms to the canonical id (the market-price path
# stores a class name; FRED's ephemeral id is normalized to fred_restricted).
_PROVIDER_ALIASES = {
    "yahoofinanceprovider": "yahoo",
    "fred": "fred_restricted",
    "fredclient": "fred_restricted",
    "webullbroker": "webull",
}


def canonical_provider(name: str | None) -> str | None:
    """Normalize a provider label to its canonical id (e.g.
    'YahooFinanceProvider' -> 'yahoo'). Returns None for an empty name; an
    unrecognized name is lower-cased and passed through unchanged."""
    if not name:
        return None
    key = name.strip().lower()
    if key in _PROVIDER_ALIASES:
        return _PROVIDER_ALIASES[key]
    return key


# --- data_quality_status reconciliation --------------------------------

_VALIDATION_TO_QUALITY = {
    "clean": DataQualityStatus.VALID,
    "available": DataQualityStatus.VALID,
    "valid": DataQualityStatus.VALID,
    "clean_with_warnings": DataQualityStatus.WARNING,
    "warning": DataQualityStatus.WARNING,
    "insufficient_data": DataQualityStatus.INSUFFICIENT,
    "insufficient": DataQualityStatus.INSUFFICIENT,
    "invalid": DataQualityStatus.INVALID,
    "quarantined": DataQualityStatus.QUARANTINED,
    "rejected": DataQualityStatus.QUARANTINED,
}


def data_quality_status_from_validation(validation_status: str | None) -> DataQualityStatus:
    """Map the legacy validation_status vocabularies (clean / clean_with_warnings
    / available / quarantined / ...) into the shared enum. An unrecognized value
    is treated conservatively as WARNING (never silently VALID)."""
    if not validation_status:
        return DataQualityStatus.WARNING
    return _VALIDATION_TO_QUALITY.get(validation_status.strip().lower(), DataQualityStatus.WARNING)


# --- timestamp helpers -------------------------------------------------


def _as_utc(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value.astimezone(dt.timezone.utc)


# Max age before daily/other-frequency data is considered stale (fallback when
# the NYSE session policy isn't applicable, e.g. macro monthly series).
_MAX_AGE_BY_FREQUENCY: dict[str, dt.timedelta] = {
    "daily": dt.timedelta(days=2),
    "1d": dt.timedelta(days=2),
    "weekly": dt.timedelta(days=9),
    "monthly": dt.timedelta(days=45),
    "quarterly": dt.timedelta(days=110),
    "annual": dt.timedelta(days=400),
}
_DEFAULT_MAX_AGE = dt.timedelta(days=2)


def compute_freshness(
    *,
    now: dt.datetime,
    source_event_timestamp: dt.datetime | None,
    available_at_timestamp: dt.datetime | None = None,
    retrieved_at_timestamp: dt.datetime | None = None,
    frequency: str | None = None,
    policy=None,
) -> Freshness:
    """Dynamically classify freshness at evaluation time `now`. NEVER persisted.

    - future_dated: the event/availability is after `now` (clock skew or a
      mislabeled record).
    - unknown: no timestamps to judge by.
    - stale/current: for daily data with a FreshnessPolicy, uses the NYSE
      session calendar; otherwise falls back to a per-frequency max age.
    """
    now = _as_utc(now)
    event = _as_utc(source_event_timestamp)
    available = _as_utc(available_at_timestamp)
    retrieved = _as_utc(retrieved_at_timestamp)

    if (event and event > now) or (available and available > now):
        return Freshness.FUTURE_DATED

    reference = event or available or retrieved
    if reference is None:
        return Freshness.UNKNOWN

    freq = (frequency or "").strip().lower() or None
    if policy is not None and event is not None and freq in (None, "daily", "1d"):
        try:
            return Freshness.STALE if policy.is_stale(event.date(), now) else Freshness.CURRENT
        except NotImplementedError:
            pass  # non-daily interval; fall through to age-based check

    max_age = _MAX_AGE_BY_FREQUENCY.get(freq, _DEFAULT_MAX_AGE)
    return Freshness.STALE if (now - reference) > max_age else Freshness.CURRENT


def validate_temporal_ordering(
    source_event_timestamp: dt.datetime | None,
    available_at_timestamp: dt.datetime | None,
    retrieved_at_timestamp: dt.datetime | None,
    *,
    is_correction: bool = False,
) -> list[str]:
    """Check the invariant source_event <= available_at <= retrieved_at (where
    each is present). Returns a list of violation messages (empty = ok).

    `is_correction=True` documents a corrected/backfilled record, which may
    legitimately have source_event after available_at (the value was revised
    after it was first knowable) - that specific check is then skipped."""
    event = _as_utc(source_event_timestamp)
    available = _as_utc(available_at_timestamp)
    retrieved = _as_utc(retrieved_at_timestamp)
    problems: list[str] = []
    if available and retrieved and available > retrieved:
        problems.append("available_at_timestamp is after retrieved_at_timestamp")
    if not is_correction and event and available and event > available:
        problems.append("source_event_timestamp is after available_at_timestamp")
    return problems


def is_point_in_time_safe(
    available_at_timestamp: dt.datetime | None, prediction_timestamp: dt.datetime
) -> bool:
    """True only if the value was knowable at prediction time. Unknown
    availability is NOT safe (returns False)."""
    if available_at_timestamp is None:
        return False
    return _as_utc(available_at_timestamp) <= _as_utc(prediction_timestamp)


def assert_point_in_time_safe(
    available_at_timestamp: dt.datetime | None, prediction_timestamp: dt.datetime
) -> None:
    """Reusable ML lookahead guard: raise LookaheadViolation if a feature would
    not have been available at prediction time (or if availability is unknown).
    An ML feature MUST pass this before being used at `prediction_timestamp`."""
    if available_at_timestamp is None:
        raise LookaheadViolation(
            "available_at_timestamp is unknown; cannot guarantee point-in-time safety."
        )
    if _as_utc(available_at_timestamp) > _as_utc(prediction_timestamp):
        raise LookaheadViolation(
            f"available_at ({_as_utc(available_at_timestamp).isoformat()}) is after prediction "
            f"time ({_as_utc(prediction_timestamp).isoformat()}); using it would leak the future."
        )


# --- The shared record -------------------------------------------------


class PointInTimeProvenance(BaseModel):
    # Five persisted facts.
    source_provider: str | None
    source_event_timestamp: dt.datetime | None
    available_at_timestamp: dt.datetime | None
    retrieved_at_timestamp: dt.datetime | None
    data_quality_status: DataQualityStatus
    # Optional source identity - recorded only where a real value exists.
    source_dataset: str | None = None
    source_series_id: str | None = None
    source_record_id: str | None = None
    source_url: str | None = None
    license_policy_id: str | None = None
    # Provider cadence, used to compute freshness (not itself a provenance fact).
    frequency: str | None = None

    def freshness(self, *, now: dt.datetime, policy=None) -> Freshness:
        return compute_freshness(
            now=now,
            source_event_timestamp=self.source_event_timestamp,
            available_at_timestamp=self.available_at_timestamp,
            retrieved_at_timestamp=self.retrieved_at_timestamp,
            frequency=self.frequency,
            policy=policy,
        )

    def temporal_violations(self, *, is_correction: bool = False) -> list[str]:
        return validate_temporal_ordering(
            self.source_event_timestamp,
            self.available_at_timestamp,
            self.retrieved_at_timestamp,
            is_correction=is_correction,
        )

    def assert_usable_for_prediction(self, prediction_timestamp: dt.datetime) -> None:
        assert_point_in_time_safe(self.available_at_timestamp, prediction_timestamp)
