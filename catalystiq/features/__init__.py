"""Application-level wiring between validated Silver/Gold storage and the ML
feature interface.

This package holds the *concrete* implementation of the ML foundation's
provider-neutral ``PointInTimeFeatureProvider`` Protocol
(:mod:`catalystiq.ml.features.provider`). It is deliberately kept OUT of the
``catalystiq.ml`` package: the ML code stays integration-free and consumes only
the abstract Protocol, while this layer is allowed to read the database and the
analysis engine. It imports the ML *schema* (the contract), never ML *model*
code, and never modifies the ML contract.
"""
from catalystiq.features.historical_dataset import (
    HistoricalDatasetAssembler,
    HistoricalDatasetResult,
    trading_timestamps,
)
from catalystiq.features.pit_provider import SilverPitFeatureProvider

__all__ = [
    "SilverPitFeatureProvider",
    "HistoricalDatasetAssembler",
    "HistoricalDatasetResult",
    "trading_timestamps",
]
