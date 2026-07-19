"""Macro endpoints (§18): read-only reads over the macro Silver products.

When FRED is enabled and configured, a request brings Silver up to date on
demand (ingest → build). When it isn't, the endpoints serve whatever is
already in Silver rather than failing - a missing optional key never breaks
an unrelated request (acceptance §6).
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from catalystiq.auth import verify_action_key
from catalystiq.db.base import get_db
from catalystiq.pipelines import macro_pipeline as mp
from catalystiq.providers.base import ProviderError, ProviderErrorCategory
from catalystiq.providers.macro import FredProvider, get_macro_provider
from catalystiq.schemas.bea import BeaValue
from catalystiq.schemas.macro import EconomicRelease, MacroObservation, MacroSeries

router = APIRouter(
    prefix="/macro",
    tags=["macro"],
    dependencies=[Depends(verify_action_key)],
)


def _provider_if_available() -> FredProvider | None:
    """The FRED provider if enabled+configured, else None (endpoints then
    serve existing Silver instead of erroring)."""
    try:
        return get_macro_provider() if _fred_enabled() else None
    except ProviderError:
        return None


def _fred_enabled() -> bool:
    from catalystiq.config import get_settings
    from catalystiq.providers.registry import is_source_configured, is_source_enabled

    settings = get_settings()
    return is_source_enabled("fred", settings) and is_source_configured("fred", settings)


def _bls_provider_if_available():
    from catalystiq.config import get_settings
    from catalystiq.providers.bls import get_bls_provider
    from catalystiq.providers.registry import is_source_configured, is_source_enabled

    settings = get_settings()
    if not (is_source_enabled("bls", settings) and is_source_configured("bls", settings)):
        return None
    try:
        return get_bls_provider()
    except ProviderError:
        return None


def _bea_provider_if_available():
    from catalystiq.config import get_settings
    from catalystiq.providers.bea import get_bea_provider
    from catalystiq.providers.registry import is_source_configured, is_source_enabled

    settings = get_settings()
    if not (is_source_enabled("bea", settings) and is_source_configured("bea", settings)):
        return None
    try:
        return get_bea_provider()
    except ProviderError:
        return None


def _series_record(row) -> MacroSeries:
    return MacroSeries(
        series_id=row.series_id,
        title=row.title,
        frequency=row.frequency,
        units=row.units,
        seasonal_adjustment=row.seasonal_adjustment,
        observation_start=row.observation_start,
        observation_end=row.observation_end,
        source=row.provider,
        retrieved_at=row.retrieved_at.replace(tzinfo=dt.timezone.utc),
    )


def _observation_record(row) -> MacroObservation:
    return MacroObservation(
        series_id=row.series_id,
        observation_date=row.observation_date,
        value=row.value,
        realtime_start=row.realtime_start,
        realtime_end=row.realtime_end,
        units=row.units,
        frequency=row.frequency,
        seasonal_adjustment=row.seasonal_adjustment,
        source_fields=row.source_fields,
        source=row.provider,
        retrieved_at=row.retrieved_at.replace(tzinfo=dt.timezone.utc),
    )


def _release_record(row) -> EconomicRelease:
    return EconomicRelease(
        release_id=row.release_id,
        name=row.name,
        scheduled_date=row.scheduled_date,
        actual_published_at=(
            row.actual_published_at.replace(tzinfo=dt.timezone.utc)
            if row.actual_published_at
            else None
        ),
        press_release=row.press_release,
        link=row.link,
        source=row.provider,
        retrieved_at=row.retrieved_at.replace(tzinfo=dt.timezone.utc),
    )


@router.get("/series/{series_id}", response_model=MacroSeries)
def get_series(series_id: str, db: Session = Depends(get_db)):
    series_id = series_id.upper()
    provider = _provider_if_available()
    row = mp.get_silver_series(db, series_id)
    if row is None and provider is not None:
        try:
            mp.ingest_series(provider, db, series_id)
        except ProviderError as exc:
            _raise_for_provider(exc)
        mp.build_silver_series(db, series_id)
        mp.build_silver_observations(db, series_id)
        row = mp.get_silver_series(db, series_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No macro series {series_id!r} available.")
    return _series_record(row)


@router.get("/series/{series_id}/observations", response_model=list[MacroObservation])
def get_observations(
    series_id: str,
    as_of: dt.date | None = Query(default=None),
    source: str = Query(default="fred", pattern="^(fred|bls)$"),
    db: Session = Depends(get_db),
):
    series_id = series_id.upper()
    rows = mp.get_silver_observations(db, series_id, provider=source, as_of=as_of)
    if not rows:
        if source == "fred":
            provider = _provider_if_available()
            if provider is not None:
                try:
                    mp.ingest_series(provider, db, series_id, as_of=as_of)
                except ProviderError as exc:
                    _raise_for_provider(exc)
                mp.build_silver_observations(db, series_id, provider="fred")
        else:  # bls
            provider = _bls_provider_if_available()
            if provider is not None:
                try:
                    mp.ingest_bls_series(provider, db, series_id)
                except ProviderError as exc:
                    _raise_for_provider(exc)
                mp.build_silver_observations(db, series_id, provider="bls")
        rows = mp.get_silver_observations(db, series_id, provider=source, as_of=as_of)
    return [_observation_record(r) for r in rows]


@router.get("/bea", response_model=list[BeaValue])
def get_bea(
    dataset: str = Query(default="NIPA"),
    table: str = Query(...),
    frequency: str = Query(default="Q", pattern="^(A|Q|M)$"),
    db: Session = Depends(get_db),
):
    from catalystiq.pipelines import bea_pipeline as bp

    rows = bp.get_silver_bea(db, dataset, table)
    if not rows:
        provider = _bea_provider_if_available()
        if provider is not None:
            try:
                bp.ingest_bea_table(provider, db, dataset, table, frequency)
            except ProviderError as exc:
                _raise_for_provider(exc)
            bp.build_silver_bea(db, dataset, table, frequency)
            rows = bp.get_silver_bea(db, dataset, table)
    return [
        BeaValue(
            dataset=r.dataset, table_name=r.table_name, line_number=r.line_number,
            line_description=r.line_description, series_code=r.series_code,
            time_period=r.time_period, frequency=r.frequency, value=r.value,
            unit=r.unit, scale=r.scale, source=r.provider,
            retrieved_at=r.retrieved_at.replace(tzinfo=dt.timezone.utc),
        )
        for r in rows
    ]


@router.get("/releases", response_model=list[EconomicRelease])
def get_releases(db: Session = Depends(get_db)):
    provider = _provider_if_available()
    rows = mp.get_silver_releases(db)
    if not rows and provider is not None:
        try:
            mp.ingest_releases(provider, db)
        except ProviderError as exc:
            _raise_for_provider(exc)
        mp.build_silver_releases(db)
        rows = mp.get_silver_releases(db)
    return [_release_record(r) for r in rows]


def _raise_for_provider(exc: ProviderError):
    status = 404 if exc.category is ProviderErrorCategory.NOT_FOUND else 502
    raise HTTPException(status_code=status, detail=str(exc)) from exc
