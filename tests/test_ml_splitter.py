"""Purged, embargoed chronological walk-forward splitter + leakage checks."""
import datetime as dt

from catalystiq.ml.validation.splitter import (
    SampleWindow,
    make_final_holdout,
    make_walk_forward_folds,
)
from catalystiq.ml.validation.leakage import (
    assert_chronological_fold,
    check_feature_target_leakage,
    check_outcome_window_purge,
)


def _samples(n=200, horizon=20):
    base = dt.datetime(2020, 1, 1)
    return [
        SampleWindow(i, base + dt.timedelta(days=i), base + dt.timedelta(days=i + horizon))
        for i in range(n)
    ]


def test_final_holdout_is_disjoint_and_purged():
    samples = _samples()
    ho = make_final_holdout(samples, holdout_fraction=0.2, embargo=dt.timedelta(days=5))
    assert set(ho.develop).isdisjoint(set(ho.holdout))
    assert ho.holdout, "holdout should not be empty"
    # Purge removed some overlapping develop samples.
    assert ho.purged_count > 0


def test_walk_forward_folds_purge_and_chronology():
    samples = _samples()
    windows = {s.index: s for s in samples}
    folds = make_walk_forward_folds(samples, n_folds=4, embargo=dt.timedelta(days=5))
    assert folds
    for f in folds:
        assert check_outcome_window_purge(f, windows).ok
        assert assert_chronological_fold(f, windows).ok
        # train, calibration, validation index sets are disjoint
        s_tr, s_ca, s_va = set(f.train), set(f.calibration), set(f.validation)
        assert s_tr.isdisjoint(s_ca)
        assert s_tr.isdisjoint(s_va)
        assert s_ca.isdisjoint(s_va)


def test_purge_actually_drops_overlapping_training_samples():
    samples = _samples()
    folds = make_walk_forward_folds(samples, n_folds=4, embargo=dt.timedelta(days=5))
    # At least one fold must have purged something (20-day windows overlap).
    assert any(f.purged_count > 0 for f in folds)


def test_feature_target_leakage_flags_perfect_correlation():
    y = [0, 1, 0, 1, 1, 0, 1]
    perfect = [float(v) for v in y]  # identical to target
    rep = check_feature_target_leakage(perfect, y, feature_name="cheater")
    assert not rep.ok
    noisy = [0.1, 0.9, 0.2, 0.6, 0.4, 0.3, 0.55]
    assert check_feature_target_leakage(noisy, y).ok


def test_empty_samples_safe():
    assert make_walk_forward_folds([]) == []
    ho = make_final_holdout([])
    assert ho.develop == [] and ho.holdout == []
