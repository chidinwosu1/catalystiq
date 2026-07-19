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

__all__ = [
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
