"""ML feature flags must fail closed."""
from catalystiq.config import Settings
from catalystiq.ml import flags
from catalystiq.ml.flags import MLDisabledError


def _settings(**over) -> Settings:
    base = dict(action_api_key="k")
    base.update(over)
    return Settings(**base)


def test_everything_disabled_by_default():
    s = _settings()
    assert not flags.ml_enabled(s).allowed
    assert not flags.training_enabled(s).allowed
    assert not flags.inference_enabled(s).allowed
    assert not flags.ranking_enabled(s).allowed
    assert not flags.behavior_model_enabled(s).allowed


def test_stage_flags_require_master_switch():
    # Stage flag on but master off => still refused.
    s = _settings(enable_ml_inference=True, enable_ml_ranking=True, enable_ml_training=True)
    assert not flags.inference_enabled(s).allowed
    assert not flags.ranking_enabled(s).allowed
    assert not flags.training_enabled(s).allowed


def test_master_and_stage_both_required():
    s = _settings(enable_ml=True, enable_ml_inference=True)
    assert flags.ml_enabled(s).allowed
    assert flags.inference_enabled(s).allowed
    # ranking still off (its own flag not set)
    assert not flags.ranking_enabled(s).allowed


def test_require_approved_models_defaults_true_and_survives_none():
    assert flags.require_approved_models(_settings()) is True
    # Unreadable settings => still true (safety rail can't be dropped).
    assert flags.require_approved_models(None) is True or flags.require_approved_models(None) is not False


def test_require_approved_models_none_is_true():
    assert flags.require_approved_models(None) is True


def test_licensing_gates_default_false():
    s = _settings()
    assert flags.fred_features_allowed(s) is False
    assert flags.twelve_data_training_allowed(s) is False
    assert flags.demo_data_allowed(s) is False


def test_behavior_model_gated_on_master():
    s = _settings(enable_aggregate_behavior_model=True)  # master off
    assert not flags.behavior_model_enabled(s).allowed
    s2 = _settings(enable_ml=True, enable_aggregate_behavior_model=True,
                   enable_behavior_model_inference=True)
    assert flags.behavior_model_enabled(s2).allowed
    assert flags.behavior_inference_enabled(s2).allowed


def test_flag_decision_require_raises():
    dec = flags.inference_enabled(_settings())
    try:
        dec.require()
        assert False, "expected MLDisabledError"
    except MLDisabledError:
        pass


def test_max_highest_conviction_capped_at_four():
    s = _settings(ml_ranker_max_highest_conviction=99)
    assert flags.max_highest_conviction(s) == 4
    s2 = _settings(ml_ranker_max_highest_conviction=2)
    assert flags.max_highest_conviction(s2) == 2
