"""Isolated, compliance-scoped FRED integration.

This package is deliberately self-contained and MUST NOT import any of the
application's persistence, analysis, scoring, or execution layers
(catalystiq.db, catalystiq.pipelines, catalystiq.analysis, catalystiq.orders).
That isolation is a compliance requirement (FRED terms: no storage, no AI/ML
use, kill-switchable) and is enforced by tests/test_fred_compliance.py.

FRED data retrieved here is used ONLY to render an ephemeral, deterministic
"Rule-Based Macroeconomic Context" panel. It is never written to a database,
cache, log, model prompt, feature store, score, backtest, saved report, or
order path. See FRED_COMPLIANCE.md for the full policy and the reviewed terms.
"""
from catalystiq.fred.allowlist import (
    ALLOWLIST,
    CopyrightStatus,
    FredSeriesSpec,
    SeriesBlocked,
    SeriesNotAllowed,
    approved_series,
    get_spec,
    require_retrievable,
)

__all__ = [
    "ALLOWLIST",
    "CopyrightStatus",
    "FredSeriesSpec",
    "SeriesBlocked",
    "SeriesNotAllowed",
    "approved_series",
    "get_spec",
    "require_retrievable",
]
