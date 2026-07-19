"""Model 5: aggregate response detection, evidence, narrative restriction."""
from catalystiq.ml.models.model_five import (
    HistoricalFrequencyResponder,
    MarketStateSnapshot,
    build_response_evidence,
    confirmation_and_failure_conditions,
    detect_antecedents,
    render_constrained_narrative,
    AntecedentType,
)


def _tables():
    return {
        "technical_level_breach": {
            "label_probs": {"breakdown_and_hold": 0.61, "breakdown_reclaim": 0.2, "mixed_or_unclear": 0.19},
            "positive": 0.19, "negative": 0.61, "reversal": 0.20, "vol_expansion": 0.54,
            "count": 734, "median_1d": -0.008, "median_5d": -0.019,
            "conf_sessions": 2, "rev_sessions": 6,
        }
    }


def test_no_antecedent_when_nothing_detected():
    st = MarketStateSnapshot("AAPL", "2026-07-19T20:00:00Z")
    ev = build_response_evidence(st, HistoricalFrequencyResponder(_tables()))
    assert ev["status"] == "no_antecedent"
    assert ev["message"] == "No validated antecedent detected"


def test_detects_technical_level_breach():
    st = MarketStateSnapshot("AAPL", "2026-07-19T20:00:00Z", prior_close_vs_sma50=1.0,
                             close_vs_sma50=-0.5, relative_volume=1.6)
    dets = detect_antecedents(st)
    assert any(d.type is AntecedentType.TECHNICAL_LEVEL_BREACH for d in dets)


def test_evidence_available_with_specific_conditions():
    st = MarketStateSnapshot("AAPL", "2026-07-19T20:00:00Z", prior_close_vs_sma50=1.0,
                             close_vs_sma50=-0.5, relative_volume=1.6)
    ev = build_response_evidence(st, HistoricalFrequencyResponder(_tables()))
    assert ev["status"] == "available"
    block = ev["antecedents"][0]
    assert block["aggregate_response"]["classification"] == "breakdown_and_hold"
    assert "SMA50" in block["confirmation_conditions"][0]


def test_confirmation_conditions_are_antecedent_specific():
    st_down = MarketStateSnapshot("X", "t", prior_close_vs_sma50=1.0, close_vs_sma50=-0.5, relative_volume=1.6)
    st_gap = MarketStateSnapshot("Y", "t", overnight_gap_pct=4.0)
    down = detect_antecedents(st_down)[0]
    gap = detect_antecedents(st_gap)[0]
    conf_down, _ = confirmation_and_failure_conditions(down)
    conf_gap, _ = confirmation_and_failure_conditions(gap)
    assert conf_down != conf_gap  # not generic across antecedents


def test_insufficient_evidence_when_thin_comparables():
    tables = {"technical_level_breach": {"label_probs": {"mixed_or_unclear": 1.0}, "count": 5}}
    st = MarketStateSnapshot("AAPL", "t", prior_close_vs_sma50=1.0, close_vs_sma50=-0.5, relative_volume=1.6)
    ev = build_response_evidence(st, HistoricalFrequencyResponder(tables))
    assert ev["antecedents"][0]["aggregate_response"]["status"] == "insufficient_evidence"


def test_narrative_only_from_structured_output():
    # No structured output (status not available/no_antecedent) => no narrative.
    assert render_constrained_narrative({"status": "error"}) is None
    st = MarketStateSnapshot("AAPL", "t", prior_close_vs_sma50=1.0, close_vs_sma50=-0.5, relative_volume=1.6)
    ev = build_response_evidence(st, HistoricalFrequencyResponder(_tables()))
    narr = render_constrained_narrative(ev)
    assert narr is not None
    # narrative must not invent a psychological claim
    for banned in ("fear", "greed", "panic", "herding"):
        assert banned not in narr.lower()
