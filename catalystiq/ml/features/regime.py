"""Point-in-time market-regime classifier (versioned, deterministic).

A transparent regime label derived ONLY from validated benchmark price bars
(e.g. SPY) as they existed at the prediction timestamp. It combines a trend
dimension (price vs the 200-day SMA and 50/200 SMA alignment) with a
volatility dimension (20-day annualized realized volatility), producing a
stable regime label and an integer code usable as an ML feature.

It is NOT a forecast and NOT a probability - it is a description of the
observable benchmark state. Like every other point-in-time input it is
computed on bars truncated to the last closed session, so it is look-ahead
invariant. When there is not enough history (needs the 200-day SMA), the
result is ``insufficient`` and the feature is recorded MISSING - never a
guessed regime.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

REGIME_VERSION = "regime_v1"

# Volatility thresholds on 20-day annualized realized volatility (percent).
CALM_MAX_VOL = 15.0
NORMAL_MAX_VOL = 30.0


class TrendState(str, Enum):
    BULL = "bull"
    BEAR = "bear"
    TRANSITIONAL = "transitional"


class VolatilityState(str, Enum):
    CALM = "calm"
    NORMAL = "normal"
    STRESSED = "stressed"


# Stable label -> integer code. The code is the ML feature value; the mapping
# is frozen (append-only) so a persisted dataset's codes never shift meaning.
REGIME_CODES: dict[str, int] = {
    "bull_calm": 1,
    "bull_normal": 2,
    "bull_stressed": 3,
    "transitional_calm": 4,
    "transitional_normal": 5,
    "transitional_stressed": 6,
    "bear_calm": 7,
    "bear_normal": 8,
    "bear_stressed": 9,
}


@dataclass(frozen=True)
class RegimeAssessment:
    insufficient: bool
    trend_state: TrendState | None
    volatility_state: VolatilityState | None
    label: str | None
    code: int | None
    version: str = REGIME_VERSION

    @property
    def available(self) -> bool:
        return not self.insufficient


def _insufficient() -> RegimeAssessment:
    return RegimeAssessment(True, None, None, None, None)


def _volatility_state(vol_annualized_pct: float) -> VolatilityState:
    if vol_annualized_pct < CALM_MAX_VOL:
        return VolatilityState.CALM
    if vol_annualized_pct < NORMAL_MAX_VOL:
        return VolatilityState.NORMAL
    return VolatilityState.STRESSED


def classify_market_regime(benchmark_bars, *, symbol: str = "SPY") -> RegimeAssessment:
    """Classify the market regime from point-in-time benchmark bars.

    ``benchmark_bars`` must already be truncated to the last closed session
    (the PIT provider does this). Returns ``insufficient`` when the 200-day SMA
    or 20-day realized volatility cannot be computed.
    """
    if not benchmark_bars or len(benchmark_bars) < 200:
        return _insufficient()

    # Reuse the validated analysis engine for the underlying statistics so the
    # regime never diverges from the rest of the system's math.
    from catalystiq.analysis.indicators import compute_technical_snapshot

    tech = compute_technical_snapshot(symbol, benchmark_bars)

    def _reading(name):
        for r in tech.indicators:
            if r.name == name and getattr(r, "value", None) is not None \
                    and getattr(r, "status", None) in (None, "computed", "available"):
                return r.value
        return None

    sma_50 = _reading("sma_50")
    sma_200 = _reading("sma_200")
    rvol = _reading("realized_volatility_20d_annualized_pct")
    close = benchmark_bars[-1].close

    if sma_50 is None or sma_200 is None or rvol is None or close is None:
        return _insufficient()

    if close > sma_200 and sma_50 >= sma_200:
        trend = TrendState.BULL
    elif close < sma_200 and sma_50 <= sma_200:
        trend = TrendState.BEAR
    else:
        trend = TrendState.TRANSITIONAL

    vol_state = _volatility_state(float(rvol))
    label = f"{trend.value}_{vol_state.value}"
    return RegimeAssessment(
        insufficient=False,
        trend_state=trend,
        volatility_state=vol_state,
        label=label,
        code=REGIME_CODES[label],
    )
