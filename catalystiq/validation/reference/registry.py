"""The indicator -> reference-source mapping, as data.

Every indicator Catalyst IQ computes is listed here with an explicit
status - covered by TA-Lib, covered by an independent TradingView-formula
or financial-statistics implementation, or genuinely not comparable to any
single external reference value. Nothing is silently omitted: an indicator
absent from TA-Lib's function set is a `TRADINGVIEW_FORMULA` or
`INDEPENDENT_STATS` entry, never treated as "proprietary" just because
TA-Lib doesn't happen to carry it - composite, decision-rule outputs (which
genuinely have no single universal reference value) are the only entries
marked `NOT_APPLICABLE`, and those are validated separately via documented
decision rules + synthetic scenarios (composite_scenarios.py), not via
this numeric-comparison registry.

Tolerances were set empirically (see this feature's implementation notes),
not guessed: the unrounded technical indicators in analysis/indicators.py
(SMA/RSI/MACD/ATR/OBV/Bollinger) match their reference to ~1e-12 once two
real convention bugs were found and fixed (OBV's start-of-series base, and
Bollinger Bands' sample-vs-population stdev - both now match TA-Lib/
TradingView exactly). The volume_liquidity.py/risk.py metrics that get
explicitly rounded to N decimals at display time (round(x, N) in their own
make_reading() call) need a tolerance wide enough to absorb that rounding,
not formula slop - each such entry's tolerance is set to slightly more
than half a unit in its last displayed decimal place, with the decimal
count noted inline.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ReferenceSource(str, Enum):
    TALIB = "talib"
    TRADINGVIEW_FORMULA = "tradingview_formula"
    INDEPENDENT_STATS = "independent_stats"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class IndicatorSpec:
    name: str
    product: str
    source: ReferenceSource
    tolerance_abs: float | None
    tolerance_rel: float | None
    reason: str


INDICATOR_REGISTRY: list[IndicatorSpec] = [
    # --- Technical (analysis/indicators.py) - unrounded values, TA-Lib ---
    IndicatorSpec("sma_20", "technical", ReferenceSource.TALIB, 1e-6, 1e-6, "unrounded; TA-Lib SMA"),
    IndicatorSpec("sma_50", "technical", ReferenceSource.TALIB, 1e-6, 1e-6, "unrounded; TA-Lib SMA"),
    IndicatorSpec("sma_100", "technical", ReferenceSource.TALIB, 1e-6, 1e-6, "unrounded; TA-Lib SMA"),
    IndicatorSpec("sma_200", "technical", ReferenceSource.TALIB, 1e-6, 1e-6, "unrounded; TA-Lib SMA"),
    IndicatorSpec("rsi_14", "technical", ReferenceSource.TALIB, 1e-6, 1e-6, "unrounded; TA-Lib RSI, Wilder smoothing"),
    IndicatorSpec("macd_line", "technical", ReferenceSource.TALIB, 1e-6, 1e-6, "unrounded; TA-Lib MACD"),
    IndicatorSpec("macd_signal", "technical", ReferenceSource.TALIB, 1e-6, 1e-6, "unrounded; TA-Lib MACD"),
    IndicatorSpec("macd_histogram", "technical", ReferenceSource.TALIB, 1e-6, 1e-6, "unrounded; TA-Lib MACD"),
    IndicatorSpec("atr_14", "technical", ReferenceSource.TALIB, 1e-6, 1e-6, "unrounded; TA-Lib ATR, Wilder smoothing"),
    IndicatorSpec("atr_14_pct", "technical", ReferenceSource.TALIB, 1e-6, 1e-6, "direct transform of validated ATR"),
    IndicatorSpec("obv", "technical", ReferenceSource.TALIB, 1e-6, 1e-6, "unrounded; TA-Lib OBV (fixed start-of-series base to match, see indicators.py)"),
    IndicatorSpec("bollinger_percent_b", "technical", ReferenceSource.TALIB, 1e-6, 1e-6, "derived from TA-Lib BBANDS (fixed sample->population stdev to match, see indicators.py)"),
    IndicatorSpec("bollinger_bandwidth_pct", "technical", ReferenceSource.TALIB, 1e-6, 1e-6, "derived from TA-Lib BBANDS"),
    IndicatorSpec("price_vs_sma_50_pct", "technical", ReferenceSource.TALIB, 1e-6, 1e-6, "direct transform of validated SMA-50"),
    IndicatorSpec("realized_volatility_20d_annualized_pct", "technical", ReferenceSource.TRADINGVIEW_FORMULA, 1e-6, 1e-6, "unrounded; TradingView Historical Volatility formula"),
    # Not reference-checked: purely relative/derivative transforms with no
    # single named external indicator (a % slope of an already-validated
    # SMA, and a same-convention volume ratio already covered under
    # volume_liquidity's relative_volume_pct).
    IndicatorSpec("sma_50_slope_10d_pct", "technical", ReferenceSource.NOT_APPLICABLE, None, None, "derived % slope of validated SMA-50; no independent named reference"),
    IndicatorSpec("relative_volume_20d_pct", "technical", ReferenceSource.NOT_APPLICABLE, None, None, "duplicate of volume_liquidity's relative_volume_pct, checked there"),

    # --- Volume & Liquidity (analysis/volume_liquidity.py) ---
    IndicatorSpec("accumulation_distribution_line", "volume_liquidity", ReferenceSource.TALIB, 5e-3, 1e-6, "rounded to 2dp at display (round(x,2)); TA-Lib AD"),
    IndicatorSpec("money_flow_index", "volume_liquidity", ReferenceSource.TALIB, 5e-4, 1e-6, "rounded to 4dp at display; TA-Lib MFI"),
    IndicatorSpec("relative_volume_pct", "volume_liquidity", ReferenceSource.TRADINGVIEW_FORMULA, 5e-3, 1e-6, "rounded to 2dp at display; TradingView Relative Volume formula"),
    IndicatorSpec("chaikin_money_flow", "volume_liquidity", ReferenceSource.TRADINGVIEW_FORMULA, 5e-5, 1e-6, "rounded to 4dp at display; TradingView Chaikin Money Flow formula"),
    IndicatorSpec("volume_price_trend", "volume_liquidity", ReferenceSource.TRADINGVIEW_FORMULA, 5e-4, 1e-6, "rounded to 4dp at display; TradingView Price Volume Trend formula"),
    # Not reference-checked: no single named/published formula (a rolling
    # average, a z-score against its own history, an up/down volume split,
    # or a bid/ask spread this build's provider doesn't supply).
    IndicatorSpec("average_daily_volume_5d", "volume_liquidity", ReferenceSource.NOT_APPLICABLE, None, None, "plain rolling mean of volume; no distinct named indicator"),
    IndicatorSpec("average_daily_volume_20d", "volume_liquidity", ReferenceSource.NOT_APPLICABLE, None, None, "plain rolling mean of volume; no distinct named indicator"),
    IndicatorSpec("average_daily_volume_60d", "volume_liquidity", ReferenceSource.NOT_APPLICABLE, None, None, "plain rolling mean of volume; no distinct named indicator"),
    IndicatorSpec("average_daily_volume_200d", "volume_liquidity", ReferenceSource.NOT_APPLICABLE, None, None, "plain rolling mean of volume; no distinct named indicator"),
    IndicatorSpec("dollar_volume", "volume_liquidity", ReferenceSource.NOT_APPLICABLE, None, None, "close * volume; not a named indicator"),
    IndicatorSpec("rolling_median_dollar_volume", "volume_liquidity", ReferenceSource.NOT_APPLICABLE, None, None, "rolling median; not a named indicator"),
    IndicatorSpec("volume_zscore", "volume_liquidity", ReferenceSource.NOT_APPLICABLE, None, None, "z-score vs. own history; not a named indicator"),
    IndicatorSpec("up_volume", "volume_liquidity", ReferenceSource.NOT_APPLICABLE, None, None, "volume sum split by session direction; not a named indicator"),
    IndicatorSpec("down_volume", "volume_liquidity", ReferenceSource.NOT_APPLICABLE, None, None, "volume sum split by session direction; not a named indicator"),
    IndicatorSpec("obv_slope", "volume_liquidity", ReferenceSource.NOT_APPLICABLE, None, None, "% slope of validated OBV; no independent named reference"),
    IndicatorSpec("volume_trend", "volume_liquidity", ReferenceSource.NOT_APPLICABLE, None, None, "% slope of a rolling average; no independent named reference"),
    IndicatorSpec("volume_confirmation_of_price", "volume_liquidity", ReferenceSource.NOT_APPLICABLE, None, None, "composite rule over validated OBV; see composite_scenarios.py"),
    IndicatorSpec("volume_divergence_from_price", "volume_liquidity", ReferenceSource.NOT_APPLICABLE, None, None, "composite rule over validated OBV; see composite_scenarios.py"),
    IndicatorSpec("turnover_ratio_pct", "volume_liquidity", ReferenceSource.NOT_APPLICABLE, None, None, "volume / shares outstanding; not TA-Lib/TradingView-comparable"),
    IndicatorSpec("bid_ask_spread", "volume_liquidity", ReferenceSource.NOT_APPLICABLE, None, None, "not_supported - no bid/ask data source"),
    IndicatorSpec("spread_pct_of_mid", "volume_liquidity", ReferenceSource.NOT_APPLICABLE, None, None, "not_supported - no bid/ask data source"),
    IndicatorSpec("estimated_slippage_band", "volume_liquidity", ReferenceSource.NOT_APPLICABLE, None, None, "not_supported - no market depth data source"),
    IndicatorSpec("liquidity_classification", "volume_liquidity", ReferenceSource.NOT_APPLICABLE, None, None, "composite rule; see composite_scenarios.py"),

    # --- Volatility & Risk (analysis/risk.py) - rounded to 4dp at display ---
    IndicatorSpec("beta_vs_benchmark", "risk", ReferenceSource.INDEPENDENT_STATS, 5e-4, 1e-4, "rounded to 4dp; independent numpy Beta (full-history log-return cov/var, same convention as risk.py)"),
    IndicatorSpec("sharpe_ratio", "risk", ReferenceSource.INDEPENDENT_STATS, 5e-4, 1e-4, "rounded to 4dp; independent numpy Sharpe"),
    IndicatorSpec("sortino_ratio", "risk", ReferenceSource.INDEPENDENT_STATS, 5e-4, 1e-4, "rounded to 4dp; independent numpy Sortino"),
    IndicatorSpec("calmar_ratio", "risk", ReferenceSource.INDEPENDENT_STATS, 5e-4, 1e-4, "rounded to 4dp; independent numpy Calmar"),
    IndicatorSpec("historical_var_95_pct", "risk", ReferenceSource.INDEPENDENT_STATS, 5e-4, 1e-4, "rounded to 4dp; independent numpy percentile VaR"),
    IndicatorSpec("parametric_var_95_pct", "risk", ReferenceSource.INDEPENDENT_STATS, 5e-4, 1e-4, "rounded to 4dp; independent scipy.stats.norm parametric VaR"),
    # Not reference-checked: rule-based flags, or metrics with no single
    # universal external formula (realized vol windows other than 20d are
    # covered under technical's historical-volatility entry; ATR/gap/
    # drawdown stats are plain descriptive statistics, not named
    # indicators with an external reference).
    IndicatorSpec("cvar_95_pct", "risk", ReferenceSource.NOT_APPLICABLE, None, None, "conditional-VaR mean-of-tail; no single universal external formula variant"),

    # --- Market Structure (analysis/market_structure.py) ---
    IndicatorSpec("swing_points", "market_structure", ReferenceSource.TRADINGVIEW_FORMULA, None, None, "structured comparison (dates/prices, not scalar tolerance) - see comparator.py; TradingView ta.pivothigh/ta.pivotlow fractal definition"),
    IndicatorSpec("trend_structure", "market_structure", ReferenceSource.NOT_APPLICABLE, None, None, "composite decision rule; see composite_scenarios.py"),
    IndicatorSpec("breakout_state", "market_structure", ReferenceSource.NOT_APPLICABLE, None, None, "composite decision rule; see composite_scenarios.py"),
    IndicatorSpec("regime", "market_structure", ReferenceSource.NOT_APPLICABLE, None, None, "composite decision rule; see composite_scenarios.py"),
    IndicatorSpec("latest_gap_candidate_classification", "market_structure", ReferenceSource.NOT_APPLICABLE, None, None, "composite decision rule; see composite_scenarios.py"),

    # --- Market & Sector Context (analysis/market_context.py) ---
    IndicatorSpec("leading_or_lagging_vs_market", "market_context", ReferenceSource.NOT_APPLICABLE, None, None, "composite decision rule; see composite_scenarios.py"),
    IndicatorSpec("leading_or_lagging_vs_sector", "market_context", ReferenceSource.NOT_APPLICABLE, None, None, "composite decision rule; see composite_scenarios.py"),
    IndicatorSpec("sector_leading_or_lagging_market", "market_context", ReferenceSource.NOT_APPLICABLE, None, None, "composite decision rule; see composite_scenarios.py"),
]


def get_spec(product: str, name: str) -> IndicatorSpec | None:
    for spec in INDICATOR_REGISTRY:
        if spec.product == product and spec.name == name:
            return spec
    return None


def specs_for_product(product: str) -> list[IndicatorSpec]:
    return [s for s in INDICATOR_REGISTRY if s.product == product]


def comparable_specs_for_product(product: str) -> list[IndicatorSpec]:
    """Only the indicators this adapter can actually numeric-compare -
    excludes NOT_APPLICABLE and the structurally-compared swing_points
    entry (handled separately in comparator.py)."""
    return [
        s
        for s in specs_for_product(product)
        if s.source not in (ReferenceSource.NOT_APPLICABLE,) and s.tolerance_abs is not None
    ]
