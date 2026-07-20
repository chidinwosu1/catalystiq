"""Point-in-time feature schema and licensing/leakage gates."""
from catalystiq.ml.features.schema import (
    DataQualityStatus,
    FeatureGroup,
    FeatureRejection,
    FeatureSpec,
    LicensingError,
    LeakageError,
    PointInTimeFeature,
    FEATURE_CATALOG,
    build_feature_vector,
    validate_feature,
)
from catalystiq.ml.features.pit_provider import SilverPointInTimeProvider

__all__ = [
    "SilverPointInTimeProvider",
    "DataQualityStatus",
    "FeatureGroup",
    "FeatureRejection",
    "FeatureSpec",
    "LicensingError",
    "LeakageError",
    "PointInTimeFeature",
    "FEATURE_CATALOG",
    "build_feature_vector",
    "validate_feature",
]
