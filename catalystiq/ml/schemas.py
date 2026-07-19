"""Stable inference-contract response shapes (Pydantic).

These define the ML API surface a validated model will eventually populate.
They are STABLE: the frontend can integrate against them now, and turning
models on later requires no shape change. When models are not trained and
approved, the endpoints return the ``not_available`` shape - never placeholder
probabilities or demo values.
"""
from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel

from catalystiq.ml import INFERENCE_CONTRACT_VERSION


class NotAvailable(BaseModel):
    status: Literal["not_available"] = "not_available"
    reason: str = "Validated and approved model artifacts do not yet exist"
    contract_version: str = INFERENCE_CONTRACT_VERSION


class RuleBasedBlock(BaseModel):
    setup_strength: float | None = None
    source: Literal["rule_based"] = "rule_based"


class ModelOneBlock(BaseModel):
    net_profit_probability: float
    target_before_stop_probability: float


class ModelTwoBlock(BaseModel):
    q10: float
    q25: float
    q50: float
    q75: float
    q90: float


class ModelThreeBlock(BaseModel):
    median_adverse_excursion: float | None
    severe_adverse_excursion: float | None
    stop_breach_probability: float | str
    gap_beyond_stop_probability: float | str
    severe_terminal_return: float | None


class ReliabilityBlock(BaseModel):
    score: int
    label: str


class GovernedDecisionBlock(BaseModel):
    status: str
    reasons: list[str] = []


class ProvenanceBlock(BaseModel):
    model_versions: dict[str, str] = {}
    feature_timestamp: dt.datetime | None = None
    data_quality: str = "unknown"


class UnifiedInference(BaseModel):
    symbol: str
    prediction_timestamp: dt.datetime
    direction: str
    horizon_days: int
    status: Literal["success"] = "success"
    contract_version: str = INFERENCE_CONTRACT_VERSION
    rule_based: RuleBasedBlock
    model_one: ModelOneBlock
    model_two: ModelTwoBlock
    model_three: ModelThreeBlock
    reliability: ReliabilityBlock
    governed_decision: GovernedDecisionBlock
    provenance: ProvenanceBlock


class RankingUnavailable(BaseModel):
    status: Literal["not_available"] = "not_available"
    ranking_type: Literal["unavailable"] = "unavailable"
    reason: str = "No sufficiently reliable opportunities identified"
    contract_version: str = INFERENCE_CONTRACT_VERSION


class RuleBasedRanking(BaseModel):
    """The pre-ML ranking: real Rule-Based Opportunity Score only, clearly
    labeled so it is never mistaken for an ML/probability result."""

    status: Literal["success"] = "success"
    ranking_type: Literal["rule_based"] = "rule_based"
    ranking_timestamp: dt.datetime
    direction: str
    horizon_days: int
    universe_size: int
    eligible_count: int
    opportunities: list[dict]
    contract_version: str = INFERENCE_CONTRACT_VERSION


class MLStatus(BaseModel):
    """Non-sensitive summary of what the ML subsystem is permitted to do."""

    enabled: bool
    training_enabled: bool
    inference_enabled: bool
    ranking_enabled: bool
    behavior_model_enabled: bool
    require_approved_models: bool
    contract_version: str = INFERENCE_CONTRACT_VERSION
    reasons: dict[str, str] = {}
