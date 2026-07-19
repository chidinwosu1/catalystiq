"""Unified inference contract + disabled endpoints fail closed."""
import datetime as dt

from catalystiq.ml.inference import (
    assemble_unified_inference,
    build_unified_from_predictions,
    ml_status,
)
from catalystiq.ml.models.model_one import Model1Prediction
from catalystiq.ml.models.model_two import Model2Prediction
from catalystiq.ml.models.model_three import Model3Prediction
from catalystiq.ml.reliability import ReliabilityInputs


PT = dt.datetime(2026, 7, 19, 20, 0, 0)


def test_endpoints_report_not_available_when_disabled(client):
    for url in ["/ml/inference/NVDA", "/ml/ranking", "/ml/behavior/AAPL"]:
        r = client.get(url)
        assert r.status_code == 200
        assert r.json()["status"] == "not_available"


def test_status_endpoint_reports_all_disabled(client):
    r = client.get("/ml/status")
    body = r.json()
    assert body["enabled"] is False
    assert body["inference_enabled"] is False
    assert body["ranking_enabled"] is False
    assert body["require_approved_models"] is True


def test_feature_requirements_endpoint(client):
    r = client.get("/ml/feature-requirements")
    assert r.status_code == 200
    assert "requirements" in r.json()


def test_registry_endpoint_empty(client):
    r = client.get("/ml/registry")
    assert r.json()["count"] == 0


def test_assemble_returns_not_available_without_approved(test_db_session):
    res = assemble_unified_inference(
        test_db_session, symbol="NVDA", prediction_timestamp=PT,
        direction="long", horizon_days=5,
    )
    assert res.status == "not_available"


def test_build_unified_contract_shape_and_governance():
    m1 = Model1Prediction(0.64, 0.58, "acceptable", 1842)
    m2 = Model2Prediction({"q10": -0.04, "q25": -0.017, "q50": 0.008, "q75": 0.026, "q90": 0.051}, 0.013, False)
    m3 = Model3Prediction(-0.013, -0.042, 0.029, 0.27, 0.04, -0.051)
    rel = ReliabilityInputs(
        feature_completeness=0.9, data_freshness_ok=True, comparable_sample_count=1842,
        out_of_distribution=False, calibration_ok=True, recent_oos_performance=0.6,
        regime_represented=True, prediction_range_width=0.1, model_agreement=0.8,
    )
    uni = build_unified_from_predictions(
        symbol="NVDA", prediction_timestamp=PT, direction="long", horizon_days=5,
        rule_based_setup_strength=79, m1=m1, m2=m2, m3=m3, reliability_inputs=rel,
        model_versions={"model_1": "1.0.0"}, data_quality="high",
    )
    assert uni.status == "success"
    assert uni.model_one.net_profit_probability == 0.64
    assert uni.model_two.q50 == 0.008
    assert uni.reliability.score > 0
    assert uni.governed_decision.status in {
        "enter_candidate", "watch", "wait", "avoid", "abstain", "insufficient_evidence"
    }


def test_governance_blocks_high_conviction_on_conflict():
    # High profit prob but negative median => conflict, no enter_candidate.
    m1 = Model1Prediction(0.7, 0.62, "acceptable", 900)
    m2 = Model2Prediction({"q10": -0.06, "q25": -0.03, "q50": -0.005, "q75": 0.01, "q90": 0.03}, -0.01, False)
    m3 = Model3Prediction(-0.02, -0.05, 0.02, 0.5, "insufficient_evidence", -0.06)
    rel = ReliabilityInputs(feature_completeness=0.9, data_freshness_ok=True,
                            comparable_sample_count=900, out_of_distribution=False,
                            calibration_ok=True, regime_represented=True)
    uni = build_unified_from_predictions(
        symbol="X", prediction_timestamp=PT, direction="long", horizon_days=5,
        rule_based_setup_strength=50, m1=m1, m2=m2, m3=m3, reliability_inputs=rel,
        model_versions={}, data_quality="high",
    )
    assert uni.governed_decision.status != "enter_candidate"


def test_ml_status_helper_reasons_present():
    st = ml_status()
    assert "enabled" in st.reasons
