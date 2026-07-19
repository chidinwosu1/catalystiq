"""Feature-schema licensing and leakage gates."""
import datetime as dt

import pytest

from catalystiq.config import Settings
from catalystiq.ml.features.schema import (
    DataQualityStatus,
    LeakageError,
    LicensingError,
    PointInTimeFeature,
    build_feature_vector,
    validate_feature,
)
from catalystiq.ml.features.manifest import manifest_dict, SourceStatus


PT = dt.datetime(2026, 1, 10, 20, 0, 0)


def _feat(**over) -> PointInTimeFeature:
    base = dict(
        symbol="AAPL",
        prediction_timestamp=PT,
        feature_name="rsi_14",
        feature_value=55.0,
        source_provider="yahoo",
        source_event_timestamp=PT - dt.timedelta(days=1),
        available_at_timestamp=PT - dt.timedelta(hours=1),
        retrieved_at_timestamp=PT,
        data_quality_status=DataQualityStatus.OK,
    )
    base.update(over)
    return PointInTimeFeature(**base)


def _settings(**over):
    base = dict(action_api_key="k")
    base.update(over)
    return Settings(**base)


def test_valid_feature_passes():
    assert validate_feature(_feat(), for_training=True, settings=_settings()) is None


def test_lookahead_rejected():
    f = _feat(available_at_timestamp=PT + dt.timedelta(minutes=1))
    rej = validate_feature(f, for_training=True, settings=_settings())
    assert rej is not None and rej.code == "leakage"


def test_fred_provider_rejected_even_if_flag_on():
    f = _feat(source_provider="fred")
    rej = validate_feature(f, for_training=True, settings=_settings(ml_allow_fred_features=True))
    assert rej is not None and rej.code == "licensing"


def test_unlicensed_alt_source_rejected():
    f = _feat(source_provider="some_sentiment_vendor")
    rej = validate_feature(f, for_training=True, settings=_settings())
    assert rej is not None and rej.code == "licensing"


def test_twelve_data_blocked_from_training_without_flag():
    f = _feat(source_provider="twelve_data")
    rej = validate_feature(f, for_training=True, settings=_settings())
    assert rej is not None and rej.code == "licensing"
    # Allowed at inference (for_training=False)
    assert validate_feature(f, for_training=False, settings=_settings()) is None
    # Allowed in training only with the explicit license flag
    ok = validate_feature(f, for_training=True, settings=_settings(ml_allow_twelve_data_training=True))
    assert ok is None


def test_missing_provenance_field_rejected():
    f = _feat(source_provider="")
    rej = validate_feature(f, for_training=True, settings=_settings())
    assert rej is not None and rej.code == "provenance"


def test_unknown_feature_name_rejected():
    f = _feat(feature_name="not_a_real_feature")
    rej = validate_feature(f, for_training=True, settings=_settings())
    assert rej is not None and rej.code == "unknown_feature"


def test_build_vector_strict_raises_on_leakage():
    feats = [_feat(available_at_timestamp=PT + dt.timedelta(minutes=1))]
    with pytest.raises(LeakageError):
        build_feature_vector(feats, for_training=True, settings=_settings(), strict=True)


def test_build_vector_strict_raises_on_licensing():
    feats = [_feat(source_provider="fred")]
    with pytest.raises(LicensingError):
        build_feature_vector(feats, for_training=True, settings=_settings(), strict=True)


def test_build_vector_emits_missing_indicators():
    vec, rej = build_feature_vector([_feat()], for_training=True, settings=_settings())
    assert vec["rsi_14"] == 55.0
    assert vec["rsi_14__is_missing"] == 0
    # A feature not provided is marked missing.
    assert vec["macd__is_missing"] == 1


def test_manifest_records_gaps_not_fabricated():
    m = manifest_dict()
    assert m["feature_schema_version"]
    # FRED-blocked macro group present but never 'wired' via FRED.
    statuses = {r["source_status"] for r in m["requirements"]}
    assert SourceStatus.UNAVAILABLE.value in statuses
    assert m["counts_by_status"]
