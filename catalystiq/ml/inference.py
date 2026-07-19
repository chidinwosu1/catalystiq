"""Unified inference-contract assembly - the single online serving point.

This is where the five model families would be combined into the stable
inference contract. It is GATED: it returns the ``not_available`` shape unless
ALL of the following hold -

  * ENABLE_ML and ENABLE_ML_INFERENCE are true, AND
  * (when ML_REQUIRE_APPROVED_MODELS is true, which is the default) approved,
    non-synthetic registry artifacts exist for Models 1-3 for the requested
    direction and horizon.

Because no approved artifacts exist yet, this currently ALWAYS returns
not_available in a deployed system. It never returns placeholder probabilities
or demo values. The pure composition helper
:func:`build_unified_from_predictions` is exercised by tests to prove the
contract shape, but it is only reachable in production through the gate.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from catalystiq.config import Settings, get_settings
from catalystiq.ml import flags, registry
from catalystiq.ml.governance import CrossModelInputs, govern
from catalystiq.ml.models.model_one import Model1Prediction
from catalystiq.ml.models.model_three import INSUFFICIENT, Model3Prediction
from catalystiq.ml.models.model_two import Model2Prediction
from catalystiq.ml.reliability import (
    ReliabilityInputs,
    assess_reliability,
    should_abstain,
)
from catalystiq.ml.schemas import (
    GovernedDecisionBlock,
    MLStatus,
    ModelOneBlock,
    ModelThreeBlock,
    ModelTwoBlock,
    NotAvailable,
    ProvenanceBlock,
    ReliabilityBlock,
    RuleBasedBlock,
    UnifiedInference,
)

CORE_FAMILIES = {"model_1", "model_2", "model_3"}


def ml_status(db: Session | None = None, settings: Settings | None = None) -> MLStatus:
    settings = settings or get_settings()
    enabled = flags.ml_enabled(settings)
    training = flags.training_enabled(settings)
    inference = flags.inference_enabled(settings)
    ranking = flags.ranking_enabled(settings)
    behavior = flags.behavior_model_enabled(settings)
    return MLStatus(
        enabled=enabled.allowed,
        training_enabled=training.allowed,
        inference_enabled=inference.allowed,
        ranking_enabled=ranking.allowed,
        behavior_model_enabled=behavior.allowed,
        require_approved_models=flags.require_approved_models(settings),
        reasons={
            "enabled": enabled.reason,
            "training": training.reason,
            "inference": inference.reason,
            "ranking": ranking.reason,
            "behavior": behavior.reason,
        },
    )


def assemble_unified_inference(
    db: Session,
    *,
    symbol: str,
    prediction_timestamp: dt.datetime,
    direction: str = "long",
    horizon_days: int = 5,
    settings: Settings | None = None,
) -> UnifiedInference | NotAvailable:
    """Gated entry point. Returns NotAvailable unless enabled AND approved
    artifacts exist. Never fabricates values."""
    settings = settings or get_settings()

    gate = flags.inference_enabled(settings)
    if not gate.allowed:
        return NotAvailable(reason=gate.reason)

    if flags.require_approved_models(settings):
        if not registry.has_approved_stack(
            db, horizon_days=horizon_days, trade_direction=direction, families=CORE_FAMILIES
        ):
            return NotAvailable(
                reason="Validated and approved model artifacts do not yet exist"
            )
        # Short direction stays unavailable until separately trained/validated.
        # (has_approved_stack already scopes to the requested direction.)

    # Approved artifacts exist but a production loader for serialized models is
    # intentionally not wired in this phase - fail closed rather than serve an
    # unloaded/guessed prediction.
    return NotAvailable(
        reason="Approved artifacts are registered but the serving loader is not enabled"
    )


def build_unified_from_predictions(
    *,
    symbol: str,
    prediction_timestamp: dt.datetime,
    direction: str,
    horizon_days: int,
    rule_based_setup_strength: float | None,
    m1: Model1Prediction,
    m2: Model2Prediction,
    m3: Model3Prediction,
    reliability_inputs: ReliabilityInputs,
    model_versions: dict[str, str],
    data_quality: str = "unknown",
) -> UnifiedInference:
    """Pure composition of the unified contract from concrete predictions.

    Applies reliability + abstention + cross-model governance. Used to prove
    the contract shape and behaviour; only reachable in production behind the
    gate in :func:`assemble_unified_inference`.
    """
    reliability = assess_reliability(reliability_inputs)

    stop_p = m3.stop_breach_probability if isinstance(m3.stop_breach_probability, (int, float)) else None
    abst = should_abstain(
        reliability_inputs,
        quantile_validation_failed=m2.quantile_crossing_detected,
    )
    gov = govern(
        CrossModelInputs(
            net_profit_probability=m1.net_profit_probability,
            target_before_stop_probability=m1.target_before_stop_probability,
            median_net_return=m2.net_return_quantiles.get("q50"),
            severe_downside=m3.severe_adverse_excursion,
            stop_breach_probability=stop_p,
            gap_beyond_stop_probability=(
                m3.gap_beyond_stop_probability
                if isinstance(m3.gap_beyond_stop_probability, (int, float))
                else None
            ),
            reliability_score=reliability.score,
            comparable_sample_count=reliability_inputs.comparable_sample_count,
            quantile_valid=not m2.quantile_crossing_detected,
        ),
        abstain_status=abst.status if abst.abstain else None,
    )

    return UnifiedInference(
        symbol=symbol,
        prediction_timestamp=prediction_timestamp,
        direction=direction,
        horizon_days=horizon_days,
        rule_based=RuleBasedBlock(setup_strength=rule_based_setup_strength),
        model_one=ModelOneBlock(
            net_profit_probability=m1.net_profit_probability,
            target_before_stop_probability=m1.target_before_stop_probability,
        ),
        model_two=ModelTwoBlock(**{k: m2.net_return_quantiles[k] for k in ("q10", "q25", "q50", "q75", "q90")}),
        model_three=ModelThreeBlock(
            median_adverse_excursion=m3.median_adverse_excursion,
            severe_adverse_excursion=m3.severe_adverse_excursion,
            stop_breach_probability=m3.stop_breach_probability,
            gap_beyond_stop_probability=m3.gap_beyond_stop_probability,
            severe_terminal_return=m3.severe_terminal_return,
        ),
        reliability=ReliabilityBlock(score=reliability.score, label=reliability.label.value),
        governed_decision=GovernedDecisionBlock(status=gov.status.value, reasons=gov.reasons),
        provenance=ProvenanceBlock(
            model_versions=model_versions,
            feature_timestamp=prediction_timestamp,
            data_quality=data_quality,
        ),
    )
