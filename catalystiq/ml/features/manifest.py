"""Machine-readable feature-requirement manifest.

The ML foundation states *which* point-in-time features each model family
needs and *whether a provider-neutral source is wired yet*. When a required
feature has no wired source, that fact is RECORDED here - never fabricated
with a placeholder value. This manifest is the single source of truth for
"what still has to be built before a model can be trained on real data".

The manifest is emitted as JSON via :func:`manifest_dict` /
:func:`write_manifest` and is asserted in tests, so drift between the code
and the documented requirements is caught automatically.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path

from catalystiq.ml import FEATURE_SCHEMA_VERSION
from catalystiq.ml.features.schema import FEATURE_CATALOG, FeatureGroup


class SourceStatus(str, Enum):
    # A provider-neutral source exists and is wired (none yet in this phase).
    WIRED = "wired"
    # The underlying integration exists in the app but is NOT yet exposed
    # through the point-in-time feature interface.
    INTEGRATION_EXISTS_NOT_PIT = "integration_exists_not_point_in_time"
    # No permitted/licensed source exists at all.
    UNAVAILABLE = "unavailable"
    # Explicitly blocked for licensing reasons (must never be sourced).
    BLOCKED = "blocked"


# Per-group source status and the reason. This is a deliberate, reviewed
# mapping - it changes only when a real point-in-time source is actually
# wired in a later phase.
_PIT = "catalystiq.ml.features.pit_provider.SilverPointInTimeProvider"

_GROUP_STATUS: dict[FeatureGroup, tuple[SourceStatus, str]] = {
    FeatureGroup.PRICE_OHLCV: (
        SourceStatus.WIRED,
        f"Wired: {_PIT} reads validated Silver bars truncated to <= prediction_timestamp.",
    ),
    FeatureGroup.TREND: (SourceStatus.WIRED, f"Wired via {_PIT} (technical snapshot on PIT bars)."),
    FeatureGroup.MOMENTUM: (SourceStatus.WIRED, f"Wired via {_PIT} (returns computed from PIT bars)."),
    FeatureGroup.OSCILLATOR: (SourceStatus.WIRED, f"Wired via {_PIT} (RSI/MACD from PIT bars)."),
    FeatureGroup.VOLATILITY: (SourceStatus.WIRED, f"Wired via {_PIT} (ATR/realized vol from PIT bars)."),
    FeatureGroup.VOLUME: (SourceStatus.WIRED, f"Wired via {_PIT} (relative volume from PIT bars)."),
    FeatureGroup.LIQUIDITY: (
        SourceStatus.WIRED,
        f"Wired via {_PIT} (estimated spread / ADV from the volume-liquidity product).",
    ),
    FeatureGroup.GAPS: (SourceStatus.WIRED, f"Wired via {_PIT} (overnight gap from PIT bars)."),
    FeatureGroup.SUPPORT_RESISTANCE: (
        SourceStatus.INTEGRATION_EXISTS_NOT_PIT,
        "Market-structure product computes levels; distance-to-level not yet mapped into the PIT provider (recorded MISSING).",
    ),
    FeatureGroup.MARKET_SECTOR: (
        SourceStatus.WIRED,
        f"Wired via {_PIT} when benchmark/sector Silver bars exist; else recorded MISSING.",
    ),
    FeatureGroup.RELATIVE_STRENGTH: (
        SourceStatus.WIRED,
        f"Wired via {_PIT} (market-context relative strength on PIT bars).",
    ),
    FeatureGroup.BETA: (SourceStatus.WIRED, f"Wired via {_PIT} (beta vs benchmark on PIT bars)."),
    FeatureGroup.REGIME: (
        SourceStatus.UNAVAILABLE,
        "A validated, versioned market-regime classifier is not yet built (recorded MISSING).",
    ),
    FeatureGroup.EARNINGS: (
        SourceStatus.UNAVAILABLE,
        "Point-in-time earnings calendar from an approved licensed source not yet wired (recorded MISSING).",
    ),
    FeatureGroup.FUNDAMENTALS: (
        SourceStatus.INTEGRATION_EXISTS_NOT_PIT,
        "SEC EDGAR filings ingested; original+amended PIT fundamentals read not yet mapped (recorded MISSING).",
    ),
    FeatureGroup.MACRO: (
        SourceStatus.INTEGRATION_EXISTS_NOT_PIT,
        "BLS/BEA ingested; as-released (vintage) PIT read not yet mapped (recorded MISSING). FRED is BLOCKED.",
    ),
    FeatureGroup.RULE_BASED: (
        SourceStatus.WIRED,
        f"Wired: {_PIT} consumes the published build_opportunity_score contract (score + factor sub-scores).",
    ),
    FeatureGroup.MISSINGNESS: (SourceStatus.WIRED, "Emitted deterministically by the feature schema."),
    FeatureGroup.DATA_QUALITY: (SourceStatus.WIRED, "Computed from feature completeness/freshness."),
}


@dataclass(frozen=True)
class FeatureRequirement:
    feature_name: str
    group: str
    description: str
    source_status: str
    source_note: str
    required_by: list[str]


# Which model families require which feature groups (coarse, for the manifest).
_REQUIRED_BY: dict[FeatureGroup, list[str]] = {
    FeatureGroup.PRICE_OHLCV: ["model_1", "model_2", "model_3", "model_4", "model_5"],
    FeatureGroup.TREND: ["model_1", "model_2", "model_3", "model_4", "model_5"],
    FeatureGroup.MOMENTUM: ["model_1", "model_2", "model_4", "model_5"],
    FeatureGroup.OSCILLATOR: ["model_1", "model_3", "model_5"],
    FeatureGroup.VOLATILITY: ["model_1", "model_2", "model_3", "model_5"],
    FeatureGroup.VOLUME: ["model_1", "model_4", "model_5"],
    FeatureGroup.LIQUIDITY: ["model_3", "model_4"],
    FeatureGroup.GAPS: ["model_3", "model_5"],
    FeatureGroup.SUPPORT_RESISTANCE: ["model_1", "model_5"],
    FeatureGroup.MARKET_SECTOR: ["model_4", "model_5"],
    FeatureGroup.RELATIVE_STRENGTH: ["model_4", "model_5"],
    FeatureGroup.BETA: ["model_3", "model_4"],
    FeatureGroup.REGIME: ["model_1", "model_2", "model_3", "model_4", "model_5"],
    FeatureGroup.EARNINGS: ["model_1", "model_3", "model_5"],
    FeatureGroup.FUNDAMENTALS: ["model_1", "model_4"],
    FeatureGroup.MACRO: ["model_2", "model_4", "model_5"],
    FeatureGroup.RULE_BASED: ["model_4"],
    FeatureGroup.MISSINGNESS: ["model_1", "model_2", "model_3", "model_4", "model_5"],
    FeatureGroup.DATA_QUALITY: ["model_1", "model_2", "model_3", "model_4", "model_5"],
}


def requirements() -> list[FeatureRequirement]:
    out: list[FeatureRequirement] = []
    for name, spec in FEATURE_CATALOG.items():
        status, note = _GROUP_STATUS.get(
            spec.group, (SourceStatus.UNAVAILABLE, "No source mapping defined.")
        )
        out.append(
            FeatureRequirement(
                feature_name=name,
                group=spec.group.value,
                description=spec.description,
                source_status=status.value,
                source_note=note,
                required_by=sorted(_REQUIRED_BY.get(spec.group, [])),
            )
        )
    return sorted(out, key=lambda r: (r.group, r.feature_name))


def manifest_dict() -> dict:
    reqs = requirements()
    blocking = [
        r for r in reqs if r.source_status in (SourceStatus.UNAVAILABLE.value,)
    ]
    return {
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "generated_by": "catalystiq.ml.features.manifest",
        "note": (
            "Machine-readable record of the point-in-time features each model "
            "family needs and whether a provider-neutral source is wired. "
            "Features without a wired source are recorded here, never "
            "fabricated. No model may train on real data until every feature "
            "it requires is 'wired'."
        ),
        "counts_by_status": _counts_by_status(reqs),
        "blocking_unavailable": sorted({r.group for r in blocking}),
        "requirements": [asdict(r) for r in reqs],
    }


def _counts_by_status(reqs: list[FeatureRequirement]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in reqs:
        counts[r.source_status] = counts.get(r.source_status, 0) + 1
    return dict(sorted(counts.items()))


def write_manifest(path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(manifest_dict(), indent=2, sort_keys=False) + "\n")
    return p
