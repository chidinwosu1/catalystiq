"""Model-artifact registry: approval guardrails and serving gate."""
import datetime as dt

import pytest

from catalystiq.ml import registry
from catalystiq.ml.registry import ArtifactApprovalError, ArtifactSpec


def _spec(**over) -> ArtifactSpec:
    base = dict(
        model_name="m1_long_5d",
        model_version="1.0.0",
        model_family="model_1",
        horizon_days=5,
        trade_direction="long",
        feature_schema_version="1.0.0",
        target_definition_version="1.0.0",
        training_data_version="data-abc123",
        evaluation_metrics={"roc_auc": 0.62},
    )
    base.update(over)
    return ArtifactSpec(**base)


def test_artifact_is_born_candidate(test_db_session):
    row = registry.register_artifact(test_db_session, _spec())
    assert row.approval_status == "candidate"


def test_get_approved_returns_none_for_candidate(test_db_session):
    registry.register_artifact(test_db_session, _spec())
    assert registry.get_approved(
        test_db_session, model_family="model_1", horizon_days=5, trade_direction="long"
    ) is None


def test_approve_then_served(test_db_session):
    row = registry.register_artifact(test_db_session, _spec())
    registry.approve(test_db_session, row.id)
    served = registry.get_approved(
        test_db_session, model_family="model_1", horizon_days=5, trade_direction="long"
    )
    assert served is not None and served.approval_status == "approved"


def test_synthetic_artifact_cannot_be_approved(test_db_session):
    row = registry.register_artifact(test_db_session, _spec(training_data_version="synthetic-xyz"))
    assert row.is_synthetic is True
    with pytest.raises(ArtifactApprovalError):
        registry.approve(test_db_session, row.id)


def test_artifact_without_metrics_cannot_be_approved(test_db_session):
    row = registry.register_artifact(test_db_session, _spec(evaluation_metrics=None))
    with pytest.raises(ArtifactApprovalError):
        registry.approve(test_db_session, row.id)


def test_has_approved_stack_requires_all_families(test_db_session):
    for fam in ("model_1", "model_2"):
        r = registry.register_artifact(test_db_session, _spec(model_name=f"{fam}_x", model_family=fam))
        registry.approve(test_db_session, r.id)
    # model_3 missing => stack incomplete
    assert not registry.has_approved_stack(
        test_db_session, horizon_days=5, trade_direction="long",
        families={"model_1", "model_2", "model_3"},
    )
    r3 = registry.register_artifact(test_db_session, _spec(model_name="m3", model_family="model_3"))
    registry.approve(test_db_session, r3.id)
    assert registry.has_approved_stack(
        test_db_session, horizon_days=5, trade_direction="long",
        families={"model_1", "model_2", "model_3"},
    )


def test_short_direction_not_served_by_long_artifact(test_db_session):
    r = registry.register_artifact(test_db_session, _spec())
    registry.approve(test_db_session, r.id)
    assert registry.get_approved(
        test_db_session, model_family="model_1", horizon_days=5, trade_direction="short"
    ) is None
