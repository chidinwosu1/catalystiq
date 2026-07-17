"""Versioned, immutable configuration for the analytical data products.

Each of the five analysis modules (indicators, market_structure, risk,
volume_liquidity, market_context) previously defined its tunables - windows,
thresholds, classification cutoffs - as bare module-level constants in a
clearly-labeled "Configuration" block. This module externalizes those
blocks into frozen, validated Pydantic models with defaults **exactly equal**
to the values that shipped earlier this session - this only relocates them,
it does not change a single formula or threshold.

Each analysis module now does e.g.
`from catalystiq.analysis.config import DEFAULT_TECHNICAL_CONFIG as _CFG` and
`RSI_PERIOD = _CFG.rsi_period` in place of the old literal - every function
body keeps referencing the same bare constant name it always did.

`configuration_version` (see `get_configuration_version()`) is a stable hash
of a product's effective config, persisted on every Gold calculation run so
two calculations that used different parameters are never conflated as
"the same result."
"""
from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class TechnicalConfig(BaseModel):
    """catalystiq/analysis/indicators.py"""

    model_config = ConfigDict(frozen=True)

    percentile_min_history_days: int = 365 * 3
    sma_windows: tuple[int, int, int, int] = (20, 50, 100, 200)
    price_vs_sma_window: int = 50
    sma_slope_window: int = 50
    sma_slope_lookback: int = 10
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bollinger_window: int = 20
    bollinger_num_std: int = 2
    atr_period: int = 14
    realized_vol_window: int = 20
    relative_volume_window: int = 20

    @field_validator(
        "percentile_min_history_days",
        "price_vs_sma_window",
        "sma_slope_window",
        "sma_slope_lookback",
        "rsi_period",
        "macd_fast",
        "macd_slow",
        "macd_signal",
        "bollinger_window",
        "bollinger_num_std",
        "atr_period",
        "realized_vol_window",
        "relative_volume_window",
    )
    @classmethod
    def _positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("must be positive")
        return v

    @field_validator("sma_windows")
    @classmethod
    def _sma_windows_positive_and_sorted(cls, v: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        if any(w <= 0 for w in v):
            raise ValueError("sma_windows must all be positive")
        if list(v) != sorted(v):
            raise ValueError("sma_windows must be ascending")
        return v

    @model_validator(mode="after")
    def _macd_ordering(self) -> "TechnicalConfig":
        if self.macd_fast >= self.macd_slow:
            raise ValueError("macd_fast must be less than macd_slow")
        return self


class MarketStructureConfig(BaseModel):
    """catalystiq/analysis/market_structure.py"""

    model_config = ConfigDict(frozen=True)

    swing_left_bars: int = 5
    swing_right_bars: int = 5
    swing_max_strength_check: int = 20
    swing_points_returned: int = 10
    level_cluster_tolerance_pct: float = 1.0
    level_broken_buffer_pct: float = 0.5
    breakout_min_penetration_pct: float = 0.5
    breakout_approach_pct: float = 2.0
    breakout_confirm_relative_volume: float = 1.2
    breakout_lookback_bars: int = 10
    range_bound_swing_change_pct: float = 1.5
    adx_period: int = 14
    adx_strong_trend: float = 25
    atr_period: int = 14
    atr_vol_window: int = 60
    vol_expansion_delta: float = 15
    vol_expansion_lookback_bars: int = 10
    sideways_vol_split_percentile: float = 50

    @field_validator(
        "swing_left_bars",
        "swing_right_bars",
        "swing_max_strength_check",
        "swing_points_returned",
        "breakout_lookback_bars",
        "adx_period",
        "atr_period",
        "atr_vol_window",
        "vol_expansion_lookback_bars",
    )
    @classmethod
    def _positive_int(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("must be positive")
        return v

    @field_validator("adx_strong_trend", "sideways_vol_split_percentile")
    @classmethod
    def _percentile_range(cls, v: float) -> float:
        if not (0 <= v <= 100):
            raise ValueError("must be between 0 and 100")
        return v


class RiskConfig(BaseModel):
    """catalystiq/analysis/risk.py"""

    model_config = ConfigDict(frozen=True)

    realized_vol_windows: tuple[int, ...] = (10, 20, 60, 252)
    downside_dev_window: int = 60
    avg_daily_range_window: int = 20
    gap_stdev_window: int = 60
    var_sample_max: int = 252
    var_confidence: float = 0.95
    correlation_window: int = 60
    trading_days_per_year: int = 252
    sharpe_risk_free_rate_annual: float = 0.0
    min_bars_for_var: int = 60
    min_bars_for_ratios: int = 61
    elevated_vol_percentile_threshold: float = 80
    extreme_atr_percentile_threshold: float = 90
    large_gap_stdev_threshold_pct: float = 2.0
    high_correlation_threshold: float = 0.8
    significant_drawdown_threshold_pct: float = -10.0
    thin_liquidity_dollar_volume_threshold: float = 1_000_000.0

    @field_validator(
        "downside_dev_window",
        "avg_daily_range_window",
        "gap_stdev_window",
        "var_sample_max",
        "correlation_window",
        "trading_days_per_year",
        "min_bars_for_var",
        "min_bars_for_ratios",
    )
    @classmethod
    def _positive_int(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("must be positive")
        return v

    @field_validator("realized_vol_windows")
    @classmethod
    def _windows_positive(cls, v: tuple[int, ...]) -> tuple[int, ...]:
        if not v or any(w <= 0 for w in v):
            raise ValueError("realized_vol_windows must be non-empty and positive")
        return v

    @field_validator("var_confidence")
    @classmethod
    def _confidence_range(cls, v: float) -> float:
        if not (0 < v < 1):
            raise ValueError("var_confidence must be between 0 and 1")
        return v


class VolumeLiquidityConfig(BaseModel):
    """catalystiq/analysis/volume_liquidity.py"""

    model_config = ConfigDict(frozen=True)

    adv_windows: tuple[int, ...] = (5, 20, 60, 200)
    relative_volume_window: int = 20
    dollar_volume_median_window: int = 20
    volume_zscore_window: int = 20
    up_down_volume_window: int = 20
    cmf_period: int = 20
    mfi_period: int = 14
    trend_slope_window: int = 10
    divergence_window: int = 20
    liquidity_high_threshold: float = 10_000_000.0
    liquidity_moderate_threshold: float = 1_000_000.0
    liquidity_low_threshold: float = 100_000.0

    @field_validator(
        "relative_volume_window",
        "dollar_volume_median_window",
        "volume_zscore_window",
        "up_down_volume_window",
        "cmf_period",
        "mfi_period",
        "trend_slope_window",
        "divergence_window",
    )
    @classmethod
    def _positive_int(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("must be positive")
        return v

    @field_validator("adv_windows")
    @classmethod
    def _windows_positive(cls, v: tuple[int, ...]) -> tuple[int, ...]:
        if not v or any(w <= 0 for w in v):
            raise ValueError("adv_windows must be non-empty and positive")
        return v

    @model_validator(mode="after")
    def _liquidity_thresholds_descending(self) -> "VolumeLiquidityConfig":
        if not (
            self.liquidity_high_threshold
            > self.liquidity_moderate_threshold
            > self.liquidity_low_threshold
            > 0
        ):
            raise ValueError(
                "liquidity thresholds must satisfy high > moderate > low > 0"
            )
        return self


class MarketContextConfig(BaseModel):
    """catalystiq/analysis/market_context.py (the SECTOR_ETF_MAP symbol
    lookup table stays in that module - it's a data mapping, not a
    calculation constant/threshold)."""

    model_config = ConfigDict(frozen=True)

    relative_return_windows: tuple[int, ...] = (1, 5, 20, 60, 252)
    beta_correlation_window: int = 60
    relative_strength_slope_window: int = 10
    leading_lagging_window: int = 20

    @field_validator("beta_correlation_window", "relative_strength_slope_window", "leading_lagging_window")
    @classmethod
    def _positive_int(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("must be positive")
        return v

    @field_validator("relative_return_windows")
    @classmethod
    def _windows_positive(cls, v: tuple[int, ...]) -> tuple[int, ...]:
        if not v or any(w <= 0 for w in v):
            raise ValueError("relative_return_windows must be non-empty and positive")
        return v


class FreshnessConfig(BaseModel):
    """catalystiq/pipelines/freshness.py. `intraday_tolerance_minutes` is
    dormant - every current caller in this codebase hardcodes
    interval="1d", so the intraday branch it feeds is unexercised by any
    real request path (see FreshnessPolicy's docstring)."""

    model_config = ConfigDict(frozen=True)

    daily_session_calendar: str = "NYSE"
    intraday_tolerance_minutes: int = 15

    @field_validator("intraday_tolerance_minutes")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("must be positive")
        return v


DEFAULT_TECHNICAL_CONFIG = TechnicalConfig()
DEFAULT_MARKET_STRUCTURE_CONFIG = MarketStructureConfig()
DEFAULT_RISK_CONFIG = RiskConfig()
DEFAULT_VOLUME_LIQUIDITY_CONFIG = VolumeLiquidityConfig()
DEFAULT_MARKET_CONTEXT_CONFIG = MarketContextConfig()
DEFAULT_FRESHNESS_CONFIG = FreshnessConfig()

PRODUCT_CONFIGS: dict[str, BaseModel] = {
    "technical": DEFAULT_TECHNICAL_CONFIG,
    "market_structure": DEFAULT_MARKET_STRUCTURE_CONFIG,
    "risk": DEFAULT_RISK_CONFIG,
    "volume_liquidity": DEFAULT_VOLUME_LIQUIDITY_CONFIG,
    "market_context": DEFAULT_MARKET_CONTEXT_CONFIG,
}


def config_hash(config: BaseModel) -> str:
    """Stable hash of a config's effective values - changes if and only if
    a value changes, independent of field declaration order."""
    payload = json.dumps(config.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def get_configuration_version(product_name: str) -> str:
    return config_hash(PRODUCT_CONFIGS[product_name])


def get_effective_config(product_name: str) -> dict:
    """The full effective configuration as a plain dict, for the audit
    snapshot persisted on GoldCalculationRun.configuration_snapshot."""
    return PRODUCT_CONFIGS[product_name].model_dump(mode="json")
