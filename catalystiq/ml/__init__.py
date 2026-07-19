"""Catalyst IQ machine-learning foundation.

This package holds the *offline* and *online* machinery for the four model
families described in the ML build spec:

    Model 1  - Net-outcome & target-before-stop classifier (two calibrated heads)
    Model 2  - Distributional net-return model (quantile regression)
    Model 3  - Path-risk & tail-loss model (adverse excursion, stop-breach, gap)
    Model 4  - Cross-sectional stock opportunity ranker

Everything here is DISABLED by default and gated behind the fail-closed
feature flags in :mod:`catalystiq.ml.flags`. The package imports cleanly
without scikit-learn (the training pipelines import it lazily), so the
disabled inference endpoint and every pure-Python utility - feature schema,
labels, splitter, calibration metrics, governance, registry, inference
contract - work in any environment.

Nothing in this package serves a user-facing prediction unless an *approved*
registry artifact exists for the requested family/direction/horizon. See
``catalystiq/ml/inference.py`` for the single assembly point and
``catalystiq/ml/registry.py`` for the approval gate.
"""

# Stable, hashable version tags. Bump the relevant one whenever the meaning
# of a schema/target/split changes so no two incompatible datasets are ever
# conflated (mirrors the analysis-config versioning convention).
FEATURE_SCHEMA_VERSION = "1.0.0"
TARGET_DEFINITION_VERSION = "1.0.0"
SPLIT_PROTOCOL_VERSION = "1.0.0"
INFERENCE_CONTRACT_VERSION = "1.0.0"

__all__ = [
    "FEATURE_SCHEMA_VERSION",
    "TARGET_DEFINITION_VERSION",
    "SPLIT_PROTOCOL_VERSION",
    "INFERENCE_CONTRACT_VERSION",
]
