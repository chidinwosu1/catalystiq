"""Data Validation Layer (§2.9).

Runs before any analysis module: verifies chronological order, dedupes
rows, flags missing trading days, flags abnormal price gaps, and
cross-checks the latest historical close against the live quote. The
result is surfaced as a data-quality banner in the UI when checks fail
(§10.4 "Data-quality warning").
"""
from __future__ import annotations

import datetime as dt
import statistics
from typing import Sequence

from catalystiq.config import get_settings
from catalystiq.schemas.market_data import OHLCVBar, Quote
from catalystiq.schemas.validation import (
    DataQualityIssue,
    DataQualityIssueType,
    DataQualityReport,
)

# Issue types that only affect evidence-quality/confidence (§2.3), not the
# pass/fail data-quality banner itself.
_NON_BLOCKING_ISSUE_TYPES = {DataQualityIssueType.THIN_HISTORY}


def check_chronological_order(bars: Sequence[OHLCVBar]) -> list[DataQualityIssue]:
    """Flags rows that arrive out of date order in the raw provider response."""
    issues = []
    for prev, curr in zip(bars, bars[1:]):
        if curr.date < prev.date:
            issues.append(
                DataQualityIssue(
                    type=DataQualityIssueType.OUT_OF_ORDER,
                    date=curr.date,
                    detail=f"Bar for {curr.date} appears after {prev.date} in provider order.",
                )
            )
    return issues


def dedupe_bars(bars: Sequence[OHLCVBar]) -> tuple[list[OHLCVBar], list[DataQualityIssue]]:
    """Drops duplicate-date rows, keeping the first occurrence."""
    seen: dict[dt.date, OHLCVBar] = {}
    issues = []
    for bar in bars:
        if bar.date in seen:
            issues.append(
                DataQualityIssue(
                    type=DataQualityIssueType.DUPLICATE_ROW,
                    date=bar.date,
                    detail=f"Duplicate row for {bar.date}; kept the first occurrence.",
                )
            )
            continue
        seen[bar.date] = bar
    return list(seen.values()), issues


def flag_missing_trading_days(
    bars: Sequence[OHLCVBar], calendar_name: str = "NYSE"
) -> list[DataQualityIssue]:
    """Flags expected exchange trading days with no bar, within the series' own date range."""
    if len(bars) < 2:
        return []

    import pandas_market_calendars as mcal

    calendar = mcal.get_calendar(calendar_name)
    start, end = bars[0].date, bars[-1].date
    schedule = calendar.schedule(start_date=start.isoformat(), end_date=end.isoformat())
    expected_days = {ts.date() for ts in schedule.index.to_pydatetime()}
    present_days = {bar.date for bar in bars}
    missing = sorted(expected_days - present_days)

    return [
        DataQualityIssue(
            type=DataQualityIssueType.MISSING_TRADING_DAY,
            date=day,
            detail=f"No price bar for {day}, an expected {calendar_name} trading day.",
        )
        for day in missing
    ]


def flag_abnormal_gaps(
    bars: Sequence[OHLCVBar], zscore_threshold: float | None = None
) -> list[DataQualityIssue]:
    """Flags open-vs-prior-close gaps whose z-score exceeds the configured threshold."""
    if len(bars) < 3:
        return []
    threshold = (
        zscore_threshold
        if zscore_threshold is not None
        else get_settings().price_gap_zscore_threshold
    )

    gap_pairs: list[tuple[OHLCVBar, float]] = []
    for prev, curr in zip(bars, bars[1:]):
        if prev.close == 0:
            continue
        gap_pairs.append((curr, (curr.open - prev.close) / prev.close))

    if len(gap_pairs) < 2:
        return []

    pct_changes = [pct for _, pct in gap_pairs]
    mean = statistics.mean(pct_changes)
    stdev = statistics.pstdev(pct_changes)
    if stdev == 0:
        return []

    issues = []
    for bar, pct in gap_pairs:
        z = (pct - mean) / stdev
        if abs(z) >= threshold:
            issues.append(
                DataQualityIssue(
                    type=DataQualityIssueType.ABNORMAL_GAP,
                    date=bar.date,
                    detail=(
                        f"Gap of {pct:.2%} on {bar.date} is {z:.1f} standard deviations "
                        "from the recent mean."
                    ),
                )
            )
    return issues


def cross_check_live_quote(
    bars: Sequence[OHLCVBar], quote: Quote | None, tolerance_pct: float = 0.02
) -> list[DataQualityIssue]:
    """Cross-checks the latest historical close against the live quote's previous close."""
    if not bars or quote is None or quote.previous_close is None:
        return []

    latest = bars[-1]
    if latest.close == 0:
        return []

    diff_pct = abs(latest.close - quote.previous_close) / latest.close
    if diff_pct > tolerance_pct:
        return [
            DataQualityIssue(
                type=DataQualityIssueType.LIVE_QUOTE_MISMATCH,
                date=latest.date,
                detail=(
                    f"Latest historical close ({latest.close}) differs from the live "
                    f"quote's previous close ({quote.previous_close}) by {diff_pct:.2%}, "
                    f"above the {tolerance_pct:.2%} tolerance."
                ),
            )
        ]
    return []


def check_history_depth(
    bars: Sequence[OHLCVBar], min_years: int | None = None
) -> list[DataQualityIssue]:
    """Flags thin history (e.g. a new IPO) so callers can lower confidence (§2.3)."""
    if not bars:
        return [
            DataQualityIssue(
                type=DataQualityIssueType.THIN_HISTORY,
                detail="No price history available.",
            )
        ]

    required_years = (
        min_years if min_years is not None else get_settings().price_history_lookback_years
    )
    span_days = (bars[-1].date - bars[0].date).days
    required_days = required_years * 365

    if span_days < required_days:
        return [
            DataQualityIssue(
                type=DataQualityIssueType.THIN_HISTORY,
                detail=(
                    f"History spans {span_days} days, short of the {required_days}-day "
                    f"({required_years}y) target used for percentile/backtest confidence."
                ),
            )
        ]
    return []


def validate_price_history(
    symbol: str,
    bars: Sequence[OHLCVBar],
    live_quote: Quote | None = None,
    calendar_name: str = "NYSE",
) -> tuple[list[OHLCVBar], DataQualityReport]:
    """Runs the full Data Validation Layer and returns (cleaned_bars, report).

    Order: chronological order is checked against the raw input first, then
    rows are deduped and sorted, then the remaining checks run against the
    cleaned series.
    """
    issues: list[DataQualityIssue] = []

    issues += check_chronological_order(bars)
    cleaned, dedupe_issues = dedupe_bars(bars)
    issues += dedupe_issues
    cleaned.sort(key=lambda b: b.date)

    issues += flag_missing_trading_days(cleaned, calendar_name)
    issues += flag_abnormal_gaps(cleaned)
    issues += cross_check_live_quote(cleaned, live_quote)
    issues += check_history_depth(cleaned)

    blocking_issues = [i for i in issues if i.type not in _NON_BLOCKING_ISSUE_TYPES]

    report = DataQualityReport(
        symbol=symbol.upper(),
        passed=len(blocking_issues) == 0,
        issues=issues,
        checked_at=dt.datetime.now(dt.timezone.utc),
        bar_count=len(cleaned),
    )
    return cleaned, report
