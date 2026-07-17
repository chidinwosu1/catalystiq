"""Cheap, generic sanity check run synchronously right after a Gold row is
persisted - NaN/inf and implausible-magnitude bounds only, no reference
library call (TA-Lib/TradingView-formula/independent-stats comparisons
never run in the synchronous request path - see
catalystiq/pipelines/market_price_pipeline.py's build_gold_*() and
catalystiq/validation/reference/scheduler.py).

Deliberately generic rather than per-indicator-bounded (e.g. "RSI must be
0-100"): tight domain bounds risk flagging legitimate extreme market
conditions as anomalies, which contradicts this codebase's established
"abnormal but real data gets flagged, not rejected" data-quality
philosophy (catalystiq/validation/data_quality.py). This only catches
genuine computational blow-ups - NaN, +/-inf, or a magnitude so large it
can only be a bug (division by near-zero, a runaway cumulative sum, ...).
"""
from __future__ import annotations

import math

_MAGNITUDE_CEILING = 1e12


def detect_anomalies(payload: dict) -> list[str]:
    """Returns the names of every indicator/metric in a Gold payload whose
    value is NaN, +/-inf, or implausibly large in magnitude. Empty list
    means clean."""
    anomalous: list[str] = []
    for key in ("indicators", "metrics"):
        for item in payload.get(key, []):
            value = item.get("value")
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            if math.isnan(value) or math.isinf(value):
                anomalous.append(item.get("name", "<unnamed>"))
            elif abs(value) > _MAGNITUDE_CEILING:
                anomalous.append(item.get("name", "<unnamed>"))
    return anomalous
