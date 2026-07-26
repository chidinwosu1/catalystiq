"""Data-quality endpoints (§18): cross-provider comparison summaries and a
manual comparison-sample trigger. Comparisons record both providers' values
and their difference; values are never averaged (§5, §16).
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from catalystiq.auth import verify_action_key
from catalystiq.config import Settings, get_settings
from catalystiq.db.base import get_db
from catalystiq.pipelines import comparison as cmp
from catalystiq.providers.base import ProviderError
from catalystiq.providers.market_data import get_webull_market_data_provider
from catalystiq.schemas.validation import ProviderComparisonRecord

router = APIRouter(
    prefix="/data-quality",
    tags=["data-quality"],
    dependencies=[Depends(verify_action_key)],
)


def _record(row) -> ProviderComparisonRecord:
    return ProviderComparisonRecord(
        domain=row.domain, symbol=row.symbol, field=row.field, as_of_date=row.as_of_date,
        primary_provider=row.primary_provider, primary_value=row.primary_value,
        secondary_provider=row.secondary_provider, secondary_value=row.secondary_value,
        absolute_diff=row.absolute_diff, relative_diff_pct=row.relative_diff_pct,
        tolerance_pct=row.tolerance_pct, within_tolerance=row.within_tolerance,
        selected_provider=row.selected_provider, selected_reason=row.selected_reason,
        created_at=row.created_at.replace(tzinfo=dt.timezone.utc),
    )


@router.get("/{domain}")
def get_data_quality(domain: str, db: Session = Depends(get_db)):
    """Summary of cross-provider comparisons for a domain."""
    return cmp.comparison_summary(db, domain=domain)


@router.post("/market_data/compare/{symbol}", response_model=ProviderComparisonRecord)
def run_comparison(
    symbol: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Manually sample a Webull-vs-Twelve-Data quote comparison and store the
    result. Requires Twelve Data enabled + configured (the price-data fallback,
    used here as the cross-check secondary)."""
    from catalystiq.providers.registry import is_source_configured, is_source_enabled
    from catalystiq.providers.twelve_data import get_twelve_data_provider

    if not (is_source_enabled("twelve_data", settings) and is_source_configured("twelve_data", settings)):
        raise HTTPException(
            status_code=400,
            detail="Twelve Data is not enabled/configured; no secondary provider to compare against.",
        )
    try:
        secondary = get_twelve_data_provider()
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        primary = get_webull_market_data_provider()
    except Exception as exc:  # Webull creds missing/unbuildable
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = cmp.compare_quotes(
        symbol, db, primary, secondary, tolerance_pct=settings.provider_comparison_tolerance_pct
    )
    return _record(row)
