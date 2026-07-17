"""Market & Sector Context data product (§14.1 of the quantitative-scoring
spec - the benchmark-relative part).

§14.2 Market breadth (advance/decline counts, percent above SMA20/50/200
across an index) needs a defined constituent universe (e.g. the real S&P 500
ticker list) to compute across - no such universe/constituent-list provider
exists in this build, so breadth is deliberately excluded here rather than
faked against an arbitrary small sample. Relative-strength/beta/correlation
against a single benchmark or sector ETF only needs that one symbol's OHLCV,
which the existing MarketDataProvider already supplies.

`SECTOR_ETF_MAP` stands in for a real sector-membership provider - callers
supply a sector name (e.g. from FundamentalsSnapshot.sector) and the router
resolves it to a sector ETF ticker.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from catalystiq.analysis.common import bars_to_frame, history_days_available, insufficient, make_reading
from catalystiq.analysis.config import DEFAULT_MARKET_CONTEXT_CONFIG as _CFG
from catalystiq.schemas.analysis import FeatureReading
from catalystiq.schemas.market_context import MarketContextSnapshot
from catalystiq.schemas.market_data import OHLCVBar

# --- Configuration - sourced from catalystiq/analysis/config.py
# (MarketContextConfig): externalized, versioned, values unchanged. ---
RELATIVE_RETURN_WINDOWS = _CFG.relative_return_windows
BETA_CORRELATION_WINDOW = _CFG.beta_correlation_window
RELATIVE_STRENGTH_SLOPE_WINDOW = _CFG.relative_strength_slope_window
LEADING_LAGGING_WINDOW = _CFG.leading_lagging_window

SECTOR_ETF_MAP = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financial Services": "XLF",
    "Financials": "XLF",
    "Energy": "XLE",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Industrials": "XLI",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Basic Materials": "XLB",
    "Communication Services": "XLC",
}


def _align_closes(a: pd.Series, b: pd.Series) -> pd.DataFrame:
    """Inner-joins two close-price series by date, since two symbols'
    provider responses may not have identical trading calendars."""
    return pd.DataFrame({"a": a, "b": b}).dropna()


def _relative_return(
    window: int, symbol_closes: pd.Series, other_closes: pd.Series
) -> float | None:
    aligned = _align_closes(symbol_closes, other_closes)
    if len(aligned) <= window:
        return None
    sym_ret = (aligned["a"].iloc[-1] - aligned["a"].iloc[-1 - window]) / aligned["a"].iloc[-1 - window] * 100
    other_ret = (aligned["b"].iloc[-1] - aligned["b"].iloc[-1 - window]) / aligned["b"].iloc[-1 - window] * 100
    return float(sym_ret - other_ret)


def _beta_correlation(symbol_closes: pd.Series, other_closes: pd.Series) -> tuple[float | None, float | None]:
    aligned = _align_closes(symbol_closes, other_closes)
    returns = aligned.pct_change().dropna()
    if len(returns) < BETA_CORRELATION_WINDOW + 1:
        return None, None
    window_returns = returns.tail(BETA_CORRELATION_WINDOW)
    var = window_returns["b"].var()
    beta = float(window_returns["a"].cov(window_returns["b"]) / var) if var > 0 else None
    correlation = float(window_returns["a"].corr(window_returns["b"]))
    return beta, correlation


def _relative_strength_trend(symbol_closes: pd.Series, other_closes: pd.Series) -> str | None:
    aligned = _align_closes(symbol_closes, other_closes)
    if len(aligned) <= RELATIVE_STRENGTH_SLOPE_WINDOW:
        return None
    rs_line = aligned["a"] / aligned["b"]
    change = (rs_line.iloc[-1] - rs_line.iloc[-1 - RELATIVE_STRENGTH_SLOPE_WINDOW]) / rs_line.iloc[-1 - RELATIVE_STRENGTH_SLOPE_WINDOW]
    if change > 0.01:
        return "rising"
    if change < -0.01:
        return "falling"
    return "flat"


def _add_relative_metrics(
    metrics: list[FeatureReading],
    label: str,
    symbol_closes: pd.Series,
    other_closes: pd.Series | None,
    other_symbol: str | None,
) -> None:
    if other_closes is None:
        for window in RELATIVE_RETURN_WINDOWS:
            metrics.append(make_reading(f"relative_return_{window}d_vs_{label}", None, f"{window}-session return relative to {label}.", {"window": window}, status="not_supported"))
        metrics.append(make_reading(f"beta_vs_{label}", None, f"Rolling beta vs {label}.", {}, status="not_supported"))
        metrics.append(make_reading(f"correlation_vs_{label}", None, f"Rolling correlation vs {label}.", {}, status="not_supported"))
        metrics.append(make_reading(f"relative_strength_trend_vs_{label}", None, f"Direction of the price/{label} ratio.", {}, status="not_supported"))
        metrics.append(make_reading(f"leading_or_lagging_vs_{label}", None, f"Whether the symbol is leading or lagging {label}.", {}, status="not_supported"))
        return

    for window in RELATIVE_RETURN_WINDOWS:
        rel_return = _relative_return(window, symbol_closes, other_closes)
        name = f"relative_return_{window}d_vs_{label}"
        desc = f"{window}-session return relative to {other_symbol or label} (excess return)."
        if rel_return is None:
            metrics.append(insufficient(name, desc, {"window": window}))
        else:
            metrics.append(make_reading(name, round(rel_return, 4), desc, {"window": window}))

    beta, correlation = _beta_correlation(symbol_closes, other_closes)
    metrics.append(make_reading(f"beta_vs_{label}", round(beta, 4) if beta is not None else None, f"Rolling {BETA_CORRELATION_WINDOW}-day beta vs {other_symbol or label}.", {"window": BETA_CORRELATION_WINDOW}, status="available" if beta is not None else "insufficient_data"))
    metrics.append(make_reading(f"correlation_vs_{label}", round(correlation, 4) if correlation is not None else None, f"Rolling {BETA_CORRELATION_WINDOW}-day correlation vs {other_symbol or label}.", {"window": BETA_CORRELATION_WINDOW}, status="available" if correlation is not None else "insufficient_data"))

    trend = _relative_strength_trend(symbol_closes, other_closes)
    metrics.append(make_reading(f"relative_strength_trend_vs_{label}", trend, f"Direction of the price/{other_symbol or label} ratio over the last {RELATIVE_STRENGTH_SLOPE_WINDOW} bars.", {"window": RELATIVE_STRENGTH_SLOPE_WINDOW}, status="available" if trend else "insufficient_data"))

    leading_return = _relative_return(LEADING_LAGGING_WINDOW, symbol_closes, other_closes)
    if leading_return is None:
        metrics.append(insufficient(f"leading_or_lagging_vs_{label}", f"Whether the symbol is leading or lagging {other_symbol or label} over {LEADING_LAGGING_WINDOW} sessions.", {"window": LEADING_LAGGING_WINDOW}))
    else:
        label_value = "leading" if leading_return > 0 else "lagging" if leading_return < 0 else "in_line"
        metrics.append(make_reading(f"leading_or_lagging_vs_{label}", label_value, f"Whether the symbol is leading or lagging {other_symbol or label} over {LEADING_LAGGING_WINDOW} sessions.", {"window": LEADING_LAGGING_WINDOW}))


def compute_market_context_snapshot(
    symbol: str,
    bars: list[OHLCVBar],
    market_bars: list[OHLCVBar] | None = None,
    market_symbol: str | None = None,
    sector_bars: list[OHLCVBar] | None = None,
    sector_symbol: str | None = None,
) -> MarketContextSnapshot:
    bars_used = len(bars)
    days_available = history_days_available(bars)
    now = dt.datetime.now(dt.timezone.utc)

    if bars_used < 2:
        return MarketContextSnapshot(
            symbol=symbol.upper(),
            market_symbol=market_symbol,
            sector_symbol=sector_symbol,
            as_of=now,
            bars_used=bars_used,
            history_days_available=days_available,
            metrics=[],
            warnings=["Not enough bars to compute market-context metrics."],
        )

    df = bars_to_frame(bars)
    symbol_closes = df["close"]

    market_closes = bars_to_frame(market_bars)["close"] if market_bars else None
    sector_closes = bars_to_frame(sector_bars)["close"] if sector_bars else None

    metrics: list[FeatureReading] = []
    _add_relative_metrics(metrics, "market", symbol_closes, market_closes, market_symbol)
    _add_relative_metrics(metrics, "sector", symbol_closes, sector_closes, sector_symbol)

    if market_closes is not None and sector_closes is not None:
        sector_vs_market = _relative_return(LEADING_LAGGING_WINDOW, sector_closes, market_closes)
        if sector_vs_market is None:
            metrics.append(insufficient("sector_leading_or_lagging_market", f"Whether the sector is leading or lagging the market over {LEADING_LAGGING_WINDOW} sessions.", {"window": LEADING_LAGGING_WINDOW}))
        else:
            label_value = "leading" if sector_vs_market > 0 else "lagging" if sector_vs_market < 0 else "in_line"
            metrics.append(make_reading("sector_leading_or_lagging_market", label_value, f"Whether the sector is leading or lagging the market over {LEADING_LAGGING_WINDOW} sessions.", {"window": LEADING_LAGGING_WINDOW}))
    else:
        metrics.append(make_reading("sector_leading_or_lagging_market", None, "Whether the sector is leading or lagging the market.", {}, status="not_supported"))

    return MarketContextSnapshot(
        symbol=symbol.upper(),
        market_symbol=market_symbol,
        sector_symbol=sector_symbol,
        as_of=now,
        bars_used=bars_used,
        history_days_available=days_available,
        metrics=metrics,
        warnings=[],
    )
