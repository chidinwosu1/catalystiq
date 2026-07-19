"""Model 4: universe eligibility, ranking, diversification, user prefs."""
from catalystiq.ml.dataset.universe import (
    AssetType,
    CandidateSnapshot,
    UniverseConfig,
    build_eligible_universe,
)
from catalystiq.ml.models.model_four import (
    Model4Ranker,
    OpportunityInputs,
    RankerExample,
    baseline_opportunity_utility,
    realized_utility,
    train_model_four,
)
from catalystiq.ml.ranking_governance import (
    DiversificationConfig,
    UserPreferences,
    apply_diversification,
    apply_user_preferences,
    highest_conviction,
)


def _snap(symbol, **over):
    base = dict(
        symbol=symbol, asset_type=AssetType.COMMON_STOCK, price=50.0,
        avg_daily_dollar_volume=20_000_000.0, estimated_spread_bps=10.0,
        history_bars=800, is_tradable=True, listed=True, sector="Tech",
        feature_staleness_days=1.0,
    )
    base.update(over)
    return CandidateSnapshot(**base)


def test_universe_excludes_penny_illiquid_otc_and_notlisted():
    cands = [
        _snap("GOOD"),
        _snap("PENNY", price=1.0),
        _snap("ILLIQ", avg_daily_dollar_volume=100.0),
        _snap("OTC", asset_type=AssetType.OTC),
        _snap("DELISTED", is_tradable=False),
        _snap("PREIPO", listed=False),
        _snap("LEV", asset_type=AssetType.LEVERAGED_INVERSE_ETF),
    ]
    members, decisions = build_eligible_universe(cands, UniverseConfig())
    eligible = {m.symbol for m in members}
    assert eligible == {"GOOD"}
    # every excluded candidate carries a reason
    for d in decisions:
        if not d.eligible:
            assert d.reasons


def test_earnings_tolerance_excludes_when_configured():
    cands = [_snap("SOON", next_earnings_in_sessions=2)]
    members, _ = build_eligible_universe(cands, UniverseConfig(), earnings_tolerance_sessions=3)
    assert members == []


def test_baseline_composite_penalizes_downside():
    good = OpportunityInputs("A", 0.6, 0.55, 0.5, 0.7, 0.7, 0.6, -0.02, 0.2, 0.02, 0.1, "Tech")
    risky = OpportunityInputs("B", 0.6, 0.55, 0.5, 0.7, 0.7, 0.6, -0.20, 0.6, 0.20, 0.8, "Tech")
    assert baseline_opportunity_utility(good) > baseline_opportunity_utility(risky)


def test_ranker_orders_by_utility():
    cands = [
        OpportunityInputs("A", 0.64, 0.58, 0.6, 0.79, 0.78, 0.7, -0.04, 0.24, 0.04, 0.1, "Tech"),
        OpportunityInputs("B", 0.50, 0.50, 0.1, 0.40, 0.30, 0.2, -0.10, 0.5, 0.2, 0.7, "Fin"),
    ]
    ranked = Model4Ranker().rank(cands)
    assert ranked[0].symbol == "A" and ranked[0].rank == 1


def test_train_model_four_refuses_in_sample_features():
    cands = [OpportunityInputs(f"S{i}", 0.6, 0.55, 0.5, 0.7, 0.7, 0.6, -0.02, 0.2, 0.02, 0.1, "Tech") for i in range(120)]
    exs = [RankerExample(c, realized_utility(net_terminal_return=0.01, severe_downside=-0.02,
           stop_breach=0, gap_risk=0, transaction_cost=0.001), oof=False) for c in cands]
    rep = train_model_four(exs)
    assert not rep.candidate_approved
    assert any("out-of-fold" in w for w in rep.warnings)


def test_diversification_caps_sector_and_preserves_raw_rank():
    ranked = Model4Ranker().rank([
        OpportunityInputs("T1", 0.9, 0.8, 0.8, 0.9, 0.9, 0.9, -0.01, 0.1, 0.01, 0.05, "Tech"),
        OpportunityInputs("T2", 0.85, 0.8, 0.8, 0.9, 0.9, 0.9, -0.01, 0.1, 0.01, 0.05, "Tech"),
        OpportunityInputs("T3", 0.80, 0.8, 0.8, 0.9, 0.9, 0.9, -0.01, 0.1, 0.01, 0.05, "Tech"),
        OpportunityInputs("F1", 0.70, 0.7, 0.6, 0.7, 0.7, 0.7, -0.02, 0.2, 0.02, 0.1, "Fin"),
    ])
    governed = apply_diversification(ranked, DiversificationConfig(max_per_sector=2))
    included = [g for g in governed if g.status == "included"]
    excluded = [g for g in governed if g.status == "excluded"]
    # 2 Tech + 1 Fin included; 1 Tech excluded with a reason.
    assert len(included) == 3
    assert excluded and excluded[0].reason
    # raw ranks preserved
    assert {g.symbol: g.raw_rank for g in governed}["T3"] == 3


def test_user_preferences_exclude_sector():
    ranked = Model4Ranker().rank([
        OpportunityInputs("T1", 0.9, 0.8, 0.8, 0.9, 0.9, 0.9, -0.01, 0.1, 0.01, 0.05, "Tech"),
        OpportunityInputs("F1", 0.7, 0.7, 0.6, 0.7, 0.7, 0.7, -0.02, 0.2, 0.02, 0.1, "Fin"),
    ])
    governed = apply_diversification(ranked)
    filtered = apply_user_preferences(governed, UserPreferences(excluded_sectors=frozenset({"Tech"})))
    included = [g for g in filtered if g.status == "included"]
    assert {g.symbol for g in included} == {"F1"}


def test_highest_conviction_capped():
    ranked = Model4Ranker().rank([
        OpportunityInputs(f"S{i}", 0.9 - i * 0.05, 0.8, 0.8, 0.9, 0.9, 0.9, -0.01, 0.1, 0.01, 0.05, f"Sec{i}")
        for i in range(8)
    ])
    governed = apply_diversification(ranked, DiversificationConfig(max_per_sector=1))
    top = highest_conviction(governed, max_names=4)
    assert len(top) == 4
