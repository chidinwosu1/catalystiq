"""Historical Silver-coverage audit for the long-history validation stage.

Before any long-history dry-run, this answers - per symbol - exactly what was
ingested and whether it COVERS the requested window, so the validation can
FAIL CLOSED on incomplete history instead of silently training on a partial or
gappy series. It reports the requested range, the earliest/latest ingested
Silver bar, the raw bar count, the expected vs present trading sessions (from
the exchange calendar), the missing sessions, and any ingestion gaps.

Nothing here fetches data or fits a model - it reads Silver bar dates and
compares them to the exchange trading calendar.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field


@dataclass
class IngestionGap:
    """A run of consecutive EXPECTED trading sessions with no ingested bar."""

    after_session: str   # last present session before the gap (or requested start)
    before_session: str  # first present session after the gap (or requested end)
    missing_sessions: int


@dataclass
class SymbolCoverage:
    symbol: str
    requested_start: str
    requested_end: str
    earliest_bar: str | None
    latest_bar: str | None
    raw_bar_count: int
    in_range_bar_count: int
    expected_sessions: int
    present_sessions: int
    missing_sessions: int
    missing_session_ratio: float
    largest_gap_sessions: int
    gaps: list[IngestionGap] = field(default_factory=list)
    missing_session_sample: list[str] = field(default_factory=list)
    complete: bool = False
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def trading_sessions(start: dt.date, end: dt.date, *, calendar_name: str = "NYSE") -> list[dt.date]:
    """Expected trading sessions in ``[start, end]`` from the exchange calendar."""
    import pandas_market_calendars as mcal

    cal = mcal.get_calendar(calendar_name)
    days = cal.valid_days(start_date=start.isoformat(), end_date=end.isoformat())
    return [d.date() if hasattr(d, "date") else d for d in days]


def audit_symbol_coverage(
    bar_dates: list[dt.date],
    *,
    symbol: str,
    start: dt.date,
    end: dt.date,
    calendar_name: str = "NYSE",
    max_missing_ratio: float = 0.02,
    max_gap_sessions: int = 5,
    start_grace_sessions: int = 3,
    end_grace_sessions: int = 3,
) -> SymbolCoverage:
    """Audit one symbol's Silver coverage of ``[start, end]``.

    ``bar_dates`` is EVERY ingested Silver bar date for the symbol. The result's
    ``complete`` is True only when the ingested history reaches the requested
    start (within ``start_grace_sessions``) and end, the missing-session ratio
    is within ``max_missing_ratio``, and no single gap exceeds
    ``max_gap_sessions``.
    """
    all_dates = sorted(set(bar_dates))
    expected = trading_sessions(start, end, calendar_name=calendar_name)
    expected_set = set(expected)
    present_in_range = sorted(d for d in all_dates if start <= d <= end)
    present_set = set(present_in_range)

    present_sessions = sum(1 for d in expected if d in present_set)
    missing = [d for d in expected if d not in present_set]
    missing_ratio = (len(missing) / len(expected)) if expected else 1.0

    gaps = _compute_gaps(expected, present_set)
    largest_gap = max((g.missing_sessions for g in gaps), default=0)

    earliest = all_dates[0] if all_dates else None
    latest = all_dates[-1] if all_dates else None

    reasons: list[str] = []
    complete = True
    if not expected:
        complete = False
        reasons.append("no expected trading sessions in the requested range (bad range?)")
    if not all_dates:
        complete = False
        reasons.append("no ingested Silver bars for this symbol")
    else:
        # History must REACH the requested start (allowing a few sessions of grace).
        first_expected = expected[0] if expected else start
        early_present = [d for d in expected if d in present_set]
        if not early_present:
            complete = False
            reasons.append("no ingested bar falls inside the requested range")
        else:
            start_offset = _sessions_between(expected, first_expected, early_present[0])
            if start_offset > start_grace_sessions:
                complete = False
                reasons.append(
                    f"history does not reach the requested start "
                    f"(first covered session {early_present[0].isoformat()} is "
                    f"{start_offset} sessions after {first_expected.isoformat()})"
                )
            last_expected = expected[-1]
            end_offset = _sessions_between(expected, early_present[-1], last_expected)
            if end_offset > end_grace_sessions:
                complete = False
                reasons.append(
                    f"history does not reach the requested end "
                    f"(last covered session {early_present[-1].isoformat()} is "
                    f"{end_offset} sessions before {last_expected.isoformat()})"
                )
    if missing_ratio > max_missing_ratio:
        complete = False
        reasons.append(
            f"missing-session ratio {missing_ratio:.3f} exceeds max {max_missing_ratio}"
        )
    if largest_gap > max_gap_sessions:
        complete = False
        reasons.append(
            f"largest ingestion gap {largest_gap} sessions exceeds max {max_gap_sessions}"
        )

    return SymbolCoverage(
        symbol=symbol.upper(),
        requested_start=start.isoformat(),
        requested_end=end.isoformat(),
        earliest_bar=earliest.isoformat() if earliest else None,
        latest_bar=latest.isoformat() if latest else None,
        raw_bar_count=len(all_dates),
        in_range_bar_count=len(present_in_range),
        expected_sessions=len(expected),
        present_sessions=present_sessions,
        missing_sessions=len(missing),
        missing_session_ratio=round(missing_ratio, 5),
        largest_gap_sessions=largest_gap,
        gaps=gaps,
        missing_session_sample=[d.isoformat() for d in missing[:20]],
        complete=complete,
        reasons=reasons,
    )


def _compute_gaps(expected: list[dt.date], present_set: set[dt.date]) -> list[IngestionGap]:
    gaps: list[IngestionGap] = []
    run: list[dt.date] = []
    last_present: dt.date | None = None
    for d in expected:
        if d in present_set:
            if run:
                gaps.append(IngestionGap(
                    after_session=(last_present.isoformat() if last_present else run[0].isoformat()),
                    before_session=d.isoformat(),
                    missing_sessions=len(run),
                ))
                run = []
            last_present = d
        else:
            run.append(d)
    if run:  # trailing gap with no present session after it
        gaps.append(IngestionGap(
            after_session=(last_present.isoformat() if last_present else run[0].isoformat()),
            before_session=run[-1].isoformat(),
            missing_sessions=len(run),
        ))
    return gaps


def _sessions_between(expected: list[dt.date], a: dt.date, b: dt.date) -> int:
    """Count of expected sessions strictly between a and b (a<=b), i.e. index
    distance. Used to measure start/end shortfall in trading days."""
    try:
        ia, ib = expected.index(a), expected.index(b)
    except ValueError:
        return len(expected)
    return abs(ib - ia)


# --- feature coverage by period --------------------------------------------
def feature_coverage_by_period(
    dated_vectors: list[tuple[dt.datetime, dict]],
    *,
    price_group_feature: str = "adj_close",
) -> dict[str, dict]:
    """Mean feature completeness and adjusted-OHLCV present-rate, bucketed by
    calendar year of the prediction date. Surfaces whether coverage degrades in
    any period (e.g. early history)."""
    from catalystiq.ml.features.schema import missing_indicator_name

    buckets: dict[str, list[dict]] = {}
    price_ind = missing_indicator_name(price_group_feature)
    for ts, vec in dated_vectors:
        buckets.setdefault(str(ts.year), []).append(vec)

    out: dict[str, dict] = {}
    for year, vecs in sorted(buckets.items()):
        n = len(vecs)
        comp = [v.get("feature_completeness") for v in vecs if v.get("feature_completeness") is not None]
        price_present = sum(1 for v in vecs if v.get(price_ind) == 0)
        out[year] = {
            "examples": n,
            "mean_completeness": round(sum(comp) / len(comp), 4) if comp else 0.0,
            "price_present_rate": round(price_present / n, 4) if n else 0.0,
        }
    return out
