"""Fundamentals endpoints (§18): read-only reads over the SEC EDGAR Silver
products. When SEC EDGAR is enabled+configured a request brings Silver up to
date on demand; otherwise it serves whatever Silver already holds.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from catalystiq.auth import verify_action_key
from catalystiq.db.base import get_db
from catalystiq.pipelines import fundamentals_pipeline as fp
from catalystiq.providers.base import ProviderError, ProviderErrorCategory
from catalystiq.providers.fundamentals import SecEdgarProvider, get_fundamentals_provider
from catalystiq.schemas.fundamentals import CompanyFact, CompanyFiling, SecurityIdentifier

router = APIRouter(tags=["fundamentals"], dependencies=[Depends(verify_action_key)])


class FundamentalsResponse(BaseModel):
    security: SecurityIdentifier
    active_facts: list[CompanyFact]


def _sec_available() -> bool:
    from catalystiq.config import get_settings
    from catalystiq.providers.registry import is_source_configured, is_source_enabled

    settings = get_settings()
    return is_source_enabled("sec_edgar", settings) and is_source_configured("sec_edgar", settings)


def _provider_if_available() -> SecEdgarProvider | None:
    try:
        return get_fundamentals_provider() if _sec_available() else None
    except ProviderError:
        return None


def _utc(value: dt.datetime | None) -> dt.datetime | None:
    return value.replace(tzinfo=dt.timezone.utc) if value is not None else None


def _ensure_company(db: Session, symbol: str) -> object | None:
    """Return the Silver security identifier row for `symbol`, ingesting on
    demand if SEC EDGAR is available and we don't have it yet."""
    row = fp.get_silver_identifier(db, symbol)
    if row is not None:
        return row
    provider = _provider_if_available()
    if provider is None:
        return None
    try:
        run = fp.ingest_company(provider, db, symbol)
    except ProviderError as exc:
        status = 404 if exc.category is ProviderErrorCategory.NOT_FOUND else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    fp.build_silver_all(db, run.requested_identifier)
    return fp.get_silver_identifier(db, symbol)


@router.get("/fundamentals/{symbol}", response_model=FundamentalsResponse)
def get_fundamentals(symbol: str, db: Session = Depends(get_db)):
    row = _ensure_company(db, symbol)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No fundamentals available for {symbol!r}.")

    security = SecurityIdentifier(
        symbol=row.symbol, cik=row.cik, name=row.name, source=row.provider,
        retrieved_at=_utc(row.retrieved_at),
    )
    facts = [
        CompanyFact(
            cik=f.cik, taxonomy=f.taxonomy, concept=f.concept, unit=f.unit, value=f.value,
            fiscal_year=f.fiscal_year, fiscal_period=f.fiscal_period,
            period_start=f.period_start, period_end=f.period_end, form=f.form,
            filing_date=f.filing_date, accession_number=f.accession_number,
            is_amendment=f.is_amendment, frame=f.frame, source=f.provider,
            retrieved_at=_utc(f.retrieved_at),
        )
        for f in fp.get_active_facts(db, row.cik)
    ]
    return FundamentalsResponse(security=security, active_facts=facts)


@router.get("/filings/{symbol}", response_model=list[CompanyFiling])
def get_filings(symbol: str, db: Session = Depends(get_db)):
    row = _ensure_company(db, symbol)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No filings available for {symbol!r}.")
    return [
        CompanyFiling(
            cik=f.cik, symbol=f.symbol, form=f.form, accession_number=f.accession_number,
            filing_date=f.filing_date, acceptance_datetime=_utc(f.acceptance_at),
            report_date=f.report_date, primary_document=f.primary_document,
            primary_doc_description=f.primary_doc_description, is_amendment=f.is_amendment,
            source_url=f.source_url, source=f.provider, retrieved_at=_utc(f.retrieved_at),
        )
        for f in fp.get_silver_filings(db, row.cik)
    ]
