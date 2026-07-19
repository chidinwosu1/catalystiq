"""Outcome labels: costs, barriers, MAE/MFE, conservative both-touch."""
from catalystiq.ml.labels.barriers import Bar, BothTouchedPolicy, compute_barrier_outcome
from catalystiq.ml.labels.costs import DEFAULT_COST_MODEL
from catalystiq.ml.labels.outcomes import generate_outcome_labels


def test_cost_model_short_costs_more_than_long():
    long_c = DEFAULT_COST_MODEL.estimate(is_short=False)
    short_c = DEFAULT_COST_MODEL.estimate(is_short=True)
    assert short_c.total > long_c.total
    assert long_c.total > 0


def test_cost_impact_scales_with_participation():
    small = DEFAULT_COST_MODEL.estimate(trade_notional=1_000, avg_daily_dollar_volume=10_000_000)
    large = DEFAULT_COST_MODEL.estimate(trade_notional=1_000_000, avg_daily_dollar_volume=10_000_000)
    assert large.impact_cost > small.impact_cost


def test_long_target_before_stop():
    path = [Bar(100, 101, 99, 100), Bar(100, 106, 99, 105)]
    o = compute_barrier_outcome(direction="long", entry_price=100, target_price=105,
                                stop_price=95, path=path)
    assert o.target_before_stop is True
    assert o.stop_breach is False


def test_long_stop_before_target():
    path = [Bar(100, 101, 94, 95)]  # low hits stop 95
    o = compute_barrier_outcome(direction="long", entry_price=100, target_price=105,
                                stop_price=95, path=path)
    assert o.target_before_stop is False
    assert o.stop_breach is True


def test_both_touched_stop_first_policy_never_favors_target():
    path = [Bar(100, 106, 94, 100)]  # touches both 105 target and 95 stop
    o = compute_barrier_outcome(direction="long", entry_price=100, target_price=105,
                                stop_price=95, path=path, both_touched_policy=BothTouchedPolicy.STOP_FIRST)
    assert o.both_touched is True
    assert o.target_before_stop is False  # never True on ambiguity


def test_both_touched_exclude_policy_returns_none():
    path = [Bar(100, 106, 94, 100)]
    o = compute_barrier_outcome(direction="long", entry_price=100, target_price=105,
                                stop_price=95, path=path, both_touched_policy=BothTouchedPolicy.EXCLUDE)
    assert o.target_before_stop is None
    assert o.excluded_reason == "both_barriers_touched_same_bar"


def test_mae_is_direction_aware_long():
    path = [Bar(100, 101, 90, 100)]  # low 90 => -10%
    o = compute_barrier_outcome(direction="long", entry_price=100, target_price=110,
                                stop_price=80, path=path)
    assert round(o.max_adverse_excursion, 4) == -0.10


def test_mae_is_direction_aware_short():
    # Short: adverse is price going UP; high 110 => -10% adverse.
    path = [Bar(100, 110, 99, 100)]
    o = compute_barrier_outcome(direction="short", entry_price=100, target_price=90,
                                stop_price=120, path=path)
    assert round(o.max_adverse_excursion, 4) == -0.10


def test_gap_beyond_stop_detected():
    # Long stop 95, but the bar opens at 92 (gapped through the stop).
    path = [Bar(92, 93, 90, 91)]
    o = compute_barrier_outcome(direction="long", entry_price=100, target_price=110,
                                stop_price=95, path=path)
    assert o.stop_breach is True
    assert o.gap_beyond_stop is True


def test_net_profit_label_after_costs():
    # Tiny gross gain wiped out by costs -> not profitable.
    path = [Bar(100, 100.05, 99.9, 100.05)]  # +0.05% gross
    labels = generate_outcome_labels(
        symbol="X", direction="long", horizon_days=1, executable_entry_price=100,
        target_price=110, stop_price=90, path=path,
    )
    assert labels.net_terminal_return < labels.gross_terminal_return
    assert labels.net_profit_label == 0
