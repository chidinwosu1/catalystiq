"""Cross-provider market-data comparison (§5, §16).

Fetches the same value from the primary (Yahoo) and secondary (Twelve Data)
providers and records a ProviderComparison: both values, their timestamps,
the absolute/relative difference, whether it's within tolerance, and which
value was selected and why. Values are NEVER averaged, and the secondary
never silently overwrites the primary - the primary is selected per the
documented source-priority rules (§16); a difference beyond tolerance is
flagged as a data-quality warning, not smoothed away.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from catalystiq.db import models
from catalystiq.providers.market_data import MarketDataError, MarketDataProvider

DOMAIN = "market_data"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _naive(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value.astimezone(dt.timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def compare_quotes(
    symbol: str,
    db: Session,
    primary: MarketDataProvider,
    secondary: MarketDataProvider,
    tolerance_pct: float,
) -> models.ProviderComparison:
    """Fetch a quote from both providers, persist and return the comparison.
    A fetch failure on either side is recorded (that side's value stays None)
    rather than aborting - the comparison still documents what was available."""
    symbol = symbol.upper()
    primary_name = getattr(primary, "PROVIDER_NAME", type(primary).__name__)
    secondary_name = getattr(secondary, "PROVIDER_NAME", type(secondary).__name__)
    # A restricted secondary (e.g. Twelve Data) may not have its raw value - or
    # any reconstructable derived value - persisted. We still compute the
    # tolerance check in memory and keep the provenance (which provider, whether
    # it agreed), but store no number that could rebuild the price.
    restricted = getattr(secondary, "RESTRICTED_NO_RAW_PERSIST", False)

    primary_value = primary_ts = None
    secondary_value = secondary_ts = None
    try:
        pq = primary.get_quote(symbol)
        primary_value, primary_ts = pq.price, _naive(pq.as_of)
    except MarketDataError:
        pass
    try:
        sq = secondary.get_quote(symbol)
        secondary_value, secondary_ts = sq.price, _naive(sq.as_of)
    except MarketDataError:
        pass

    abs_diff = rel_diff = None
    within = True
    if primary_value is not None and secondary_value is not None:
        abs_diff = abs(primary_value - secondary_value)
        base = abs(primary_value) or 1.0
        rel_diff = (abs_diff / base) * 100.0
        within = rel_diff <= tolerance_pct

    # Source priority (§16): the primary (Yahoo) is authoritative for
    # historical analytical data; the secondary is validation only. Never
    # averaged.
    if primary_value is not None:
        selected_provider = primary_name
        reason = "primary provider per source-priority rules (§16); secondary is validation-only"
    elif secondary_value is not None:
        selected_provider = secondary_name
        reason = "primary unavailable; secondary used as explicit fallback"
    else:
        selected_provider = primary_name
        reason = "neither provider returned a value"

    if not within:
        if restricted:
            # No numeric diff in the stored reason - it would reconstruct the value.
            reason += "; secondary differs beyond tolerance (data-quality warning)"
        else:
            reason += (
                f"; difference {rel_diff:.3f}% exceeds tolerance {tolerance_pct}% "
                "(data-quality warning)"
            )

    # For a restricted secondary, persist the tolerance OUTCOME + provenance only
    # - never the raw value, its timestamp, or a reconstructable difference.
    stored_secondary_value = None if restricted else secondary_value
    stored_secondary_ts = None if restricted else secondary_ts
    stored_abs_diff = None if restricted else abs_diff
    stored_rel_diff = None if restricted else rel_diff

    row = models.ProviderComparison(
        domain=DOMAIN,
        symbol=symbol,
        field="quote_price",
        as_of_date=dt.date.today(),
        primary_provider=primary_name,
        primary_value=primary_value,
        primary_timestamp=primary_ts,
        secondary_provider=secondary_name,
        secondary_value=stored_secondary_value,
        secondary_timestamp=stored_secondary_ts,
        absolute_diff=stored_abs_diff,
        relative_diff_pct=stored_rel_diff,
        tolerance_pct=tolerance_pct,
        within_tolerance=within,
        selected_provider=selected_provider,
        selected_reason=reason,
        created_at=_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_ohlcv_with_fallback(
    symbol: str,
    start: dt.date,
    primary: MarketDataProvider,
    secondary: MarketDataProvider | None,
    interval: str = "1d",
) -> tuple[list, str]:
    """Primary first; only if it raises AND a secondary is provided (i.e. the
    caller explicitly enabled fallback) does it try the secondary. Returns
    (bars, provider_used). The secondary is never used pre-emptively."""
    primary_name = getattr(primary, "PROVIDER_NAME", type(primary).__name__)
    try:
        return primary.get_ohlcv(symbol, start=start, interval=interval), primary_name
    except MarketDataError:
        if secondary is None:
            raise
        secondary_name = getattr(secondary, "PROVIDER_NAME", type(secondary).__name__)
        return secondary.get_ohlcv(symbol, start=start, interval=interval), secondary_name


def comparison_summary(db: Session, domain: str = DOMAIN) -> dict:
    rows = db.query(models.ProviderComparison).filter_by(domain=domain).all()
    out_of_tol = [r for r in rows if not r.within_tolerance]
    return {
        "domain": domain,
        "total_comparisons": len(rows),
        "within_tolerance": len(rows) - len(out_of_tol),
        "out_of_tolerance": len(out_of_tol),
        "out_of_tolerance_symbols": sorted({r.symbol for r in out_of_tol}),
    }
