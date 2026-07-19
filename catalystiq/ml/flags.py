"""Fail-closed decision point for every ML capability.

Every question of the form "may we do X with ML?" is answered *here* and
nowhere else, so the safety posture is auditable in one file. The cardinal
rule is FAIL CLOSED: any error, any missing/invalid setting, any exception
reading configuration resolves to "not permitted". A capability is never
enabled by omission or by accident.

The public helpers return a :class:`FlagDecision` - a small (allowed, reason)
pair - rather than a bare bool, so callers can surface *why* a capability is
unavailable (an audit/observability requirement) without re-deriving the
logic. ``.require()`` raises :class:`MLDisabledError` for the common
"refuse loudly" path used by the online endpoint.
"""
from __future__ import annotations

from dataclasses import dataclass

from catalystiq.config import Settings, get_settings


class MLDisabledError(RuntimeError):
    """Raised when an ML capability is invoked while disabled or ungated.

    Carries a human-readable reason (never a secret) so the caller can return
    a stable ``not_available`` response instead of a 500.
    """


@dataclass(frozen=True)
class FlagDecision:
    allowed: bool
    reason: str

    def require(self) -> None:
        if not self.allowed:
            raise MLDisabledError(self.reason)

    def __bool__(self) -> bool:  # let callers use it in an ``if`` directly
        return self.allowed


def _as_bool(value: object) -> bool:
    """Coerce a setting to a strict bool, failing closed on anything odd.

    pydantic already parses ``ENABLE_ML`` into a real bool, but this subsystem
    treats configuration as adversarial: a stray string, ``None``, or a value
    that raises on truthiness must resolve to False, never True.
    """
    try:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value == 1
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
    except Exception:
        return False
    return False


def _settings(settings: Settings | None) -> Settings | None:
    if settings is not None:
        return settings
    try:
        return get_settings()
    except Exception:
        # Configuration could not even be loaded - fail closed.
        return None


def ml_enabled(settings: Settings | None = None) -> FlagDecision:
    """The master switch. Everything else is gated on this first."""
    s = _settings(settings)
    if s is None:
        return FlagDecision(False, "ML configuration could not be loaded")
    if not _as_bool(getattr(s, "enable_ml", False)):
        return FlagDecision(False, "ML subsystem is disabled (ENABLE_ML=false)")
    return FlagDecision(True, "ML subsystem enabled")


def _gated(flag_name: str, disabled_reason: str, settings: Settings | None) -> FlagDecision:
    """master-switch AND a specific stage flag, both fail-closed."""
    master = ml_enabled(settings)
    if not master.allowed:
        return master
    s = _settings(settings)
    if s is None or not _as_bool(getattr(s, flag_name, False)):
        return FlagDecision(False, disabled_reason)
    return FlagDecision(True, "permitted")


def training_enabled(settings: Settings | None = None) -> FlagDecision:
    return _gated(
        "enable_ml_training",
        "ML training is disabled (ENABLE_ML_TRAINING=false)",
        settings,
    )


def inference_enabled(settings: Settings | None = None) -> FlagDecision:
    return _gated(
        "enable_ml_inference",
        "ML inference is disabled (ENABLE_ML_INFERENCE=false)",
        settings,
    )


def ranking_enabled(settings: Settings | None = None) -> FlagDecision:
    return _gated(
        "enable_ml_ranking",
        "ML opportunity ranking is disabled (ENABLE_ML_RANKING=false)",
        settings,
    )


def require_approved_models(settings: Settings | None = None) -> bool:
    """Whether user-facing predictions must come from approved artifacts.

    Defaults TRUE and, critically, is ALSO true whenever settings can't be
    read - the safety rail can never be silently dropped. It only returns
    False if ML is enabled and the operator explicitly set it false.
    """
    s = _settings(settings)
    if s is None:
        return True
    return _require(s, "ml_require_approved_models")


def require_approved_ranker(settings: Settings | None = None) -> bool:
    s = _settings(settings)
    if s is None:
        return True
    return _require(s, "ml_ranker_require_approved_model")


def _require(s: Settings, name: str) -> bool:
    """Read a require-* rail. Absent/unreadable => True (stay safe)."""
    try:
        val = getattr(s, name)
    except Exception:
        return True
    if val is None:
        return True
    return _as_bool(val)


def fred_features_allowed(settings: Settings | None = None) -> bool:
    """FRED-derived values in ML features. Defaults false; the feature schema
    blocks FRED regardless (defense in depth), so flipping this true is not by
    itself sufficient to admit FRED - a second, deliberate schema change is."""
    s = _settings(settings)
    if s is None:
        return False
    return _as_bool(getattr(s, "ml_allow_fred_features", False))


def twelve_data_training_allowed(settings: Settings | None = None) -> bool:
    """Twelve Data into training. Requires an explicit licensing flag; false
    when settings can't be read."""
    s = _settings(settings)
    if s is None:
        return False
    return _as_bool(getattr(s, "ml_allow_twelve_data_training", False))


def demo_data_allowed(settings: Settings | None = None) -> bool:
    """Synthetic/demo data backing a *user-facing* artifact. Always false in
    practice; synthetic data is for unit tests only."""
    s = _settings(settings)
    if s is None:
        return False
    return _as_bool(getattr(s, "ml_ranker_allow_demo_data", False))


def max_highest_conviction(settings: Settings | None = None) -> int:
    s = _settings(settings)
    default = 4
    if s is None:
        return default
    try:
        val = int(getattr(s, "ml_ranker_max_highest_conviction", default))
    except Exception:
        return default
    # Never exceed the product cap of four, never negative.
    return max(0, min(val, 4))


def behavior_model_enabled(settings: Settings | None = None) -> FlagDecision:
    """Model 5 master switch. Gated on ENABLE_ML first, then its own flag."""
    master = ml_enabled(settings)
    if not master.allowed:
        return master
    s = _settings(settings)
    if s is None or not _as_bool(getattr(s, "enable_aggregate_behavior_model", False)):
        return FlagDecision(
            False, "Aggregate behavior model is disabled (ENABLE_AGGREGATE_BEHAVIOR_MODEL=false)"
        )
    return FlagDecision(True, "permitted")


def behavior_training_enabled(settings: Settings | None = None) -> FlagDecision:
    base = behavior_model_enabled(settings)
    if not base.allowed:
        return base
    s = _settings(settings)
    if s is None or not _as_bool(getattr(s, "enable_behavior_model_training", False)):
        return FlagDecision(False, "Behavior model training is disabled")
    return FlagDecision(True, "permitted")


def behavior_inference_enabled(settings: Settings | None = None) -> FlagDecision:
    base = behavior_model_enabled(settings)
    if not base.allowed:
        return base
    s = _settings(settings)
    if s is None or not _as_bool(getattr(s, "enable_behavior_model_inference", False)):
        return FlagDecision(False, "Behavior model inference is disabled")
    return FlagDecision(True, "permitted")


def behavior_require_approved_artifact(settings: Settings | None = None) -> bool:
    s = _settings(settings)
    if s is None:
        return True
    return _require(s, "behavior_model_require_approved_artifact")


def behavior_fred_allowed(settings: Settings | None = None) -> bool:
    s = _settings(settings)
    if s is None:
        return False
    return _as_bool(getattr(s, "behavior_model_allow_fred", False))


def behavior_twelve_data_training_allowed(settings: Settings | None = None) -> bool:
    s = _settings(settings)
    if s is None:
        return False
    return _as_bool(getattr(s, "behavior_model_allow_twelve_data_training", False))


def behavior_demo_data_allowed(settings: Settings | None = None) -> bool:
    s = _settings(settings)
    if s is None:
        return False
    return _as_bool(getattr(s, "behavior_model_allow_demo_data", False))


def max_opportunity_table(settings: Settings | None = None) -> int:
    s = _settings(settings)
    default = 25
    if s is None:
        return default
    try:
        val = int(getattr(s, "ml_ranker_max_opportunity_table", default))
    except Exception:
        return default
    return max(0, val)
