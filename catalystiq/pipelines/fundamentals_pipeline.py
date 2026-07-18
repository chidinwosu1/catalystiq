"""Bronze -> Silver pipeline for the fundamentals domain (§6).

    SecEdgarProvider -> BronzeRawDocument -> SilverSecurityIdentifier
                                          -> SilverCompanyFiling
                                          -> SilverCompanyFact
                                          -> SilverMaterialEvent

All raw documents for a company are keyed by its 10-digit CIK (the stable
identifier; tickers can change or be reused). Amended filings/facts are
preserved as distinct rows keyed by accession number - an amendment never
overwrites the originally-filed value; the active value is the latest-filed
one (get_active_facts()).
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from catalystiq.db import models
from catalystiq.pipelines.ingestion import (
    finish_ingestion_run,
    latest_raw_document,
    start_ingestion_run,
    store_raw_document,
)
from catalystiq.providers.base import LicenseClassification, ProviderError, ProviderErrorCategory
from catalystiq.providers.fundamentals import SecEdgarProvider
from catalystiq.schemas.fundamentals import (
    CompanyFact,
    CompanyFiling,
    MaterialEvent,
    SecurityIdentifier,
)

DOMAIN = "fundamentals"
NORMALIZATION_VERSION = "1.0.0"

_STATUS_BY_CATEGORY = {
    ProviderErrorCategory.RATE_LIMITED: "rate_limited",
    ProviderErrorCategory.TIMEOUT: "unavailable",
    ProviderErrorCategory.NETWORK: "unavailable",
    ProviderErrorCategory.UNAVAILABLE: "unavailable",
}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _naive(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value.astimezone(dt.timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def _mixin(*, stable_identifier, provider, source_record_id, effective_at, run_id, warnings=None):
    now = _now()
    return dict(
        stable_identifier=stable_identifier,
        provider=provider,
        source_record_id=source_record_id,
        source_available_at=None,
        effective_at=effective_at,
        retrieved_at=now,
        bronze_ingestion_run_id=run_id,
        validation_status="clean_with_warnings" if warnings else "clean",
        data_quality_warnings=warnings or None,
        normalization_version=NORMALIZATION_VERSION,
    )


# --- Bronze -------------------------------------------------------------


def ingest_company(provider: SecEdgarProvider, db: Session, symbol: str) -> models.BronzeIngestionRun:
    """Resolve the symbol to a CIK, then fetch and store filings, material
    events, and XBRL facts as raw documents under one ingestion run."""
    symbol = symbol.upper()
    run = start_ingestion_run(
        db,
        domain=DOMAIN,
        provider=provider.PROVIDER_NAME,
        adapter_version=provider.ADAPTER_VERSION,
        dataset="submissions+companyfacts",
        requested_symbol=symbol,
        requested_identifier=symbol,
        license_classification=LicenseClassification.PUBLIC_DOMAIN.value,
        data_classification="end_of_day",
    )
    try:
        identifier = provider.resolve_cik(symbol)
        cik = identifier.cik
        filings = provider.get_filings(cik)
        material = provider.get_material_events(cik)
        facts = provider.get_company_facts(cik)
    except ProviderError as exc:
        status = _STATUS_BY_CATEGORY.get(exc.category, "failed")
        finish_ingestion_run(
            db, run, status=status, error_category=exc.category.value, error_detail=str(exc)
        )
        db.commit()
        raise

    run.requested_identifier = cik
    store_raw_document(
        db, run, source_identifier=cik, document_type="security_identifier",
        payload=identifier.model_dump(mode="json"),
    )
    store_raw_document(
        db, run, source_identifier=cik, document_type="filings",
        payload={"filings": [f.model_dump(mode="json") for f in filings]},
    )
    store_raw_document(
        db, run, source_identifier=cik, document_type="material_events",
        payload={"events": [e.model_dump(mode="json") for e in material]},
    )
    store_raw_document(
        db, run, source_identifier=cik, document_type="company_facts",
        payload={"facts": [f.model_dump(mode="json") for f in facts]},
    )
    finish_ingestion_run(db, run, status="succeeded", record_count=len(filings) + len(facts))
    db.commit()
    db.refresh(run)
    return run


# --- Silver -------------------------------------------------------------


def build_silver_identifier(db: Session, cik: str, provider: str = "sec_edgar") -> bool:
    doc = latest_raw_document(
        db, domain=DOMAIN, source_identifier=cik, document_type="security_identifier"
    )
    if doc is None:
        return False
    ident = SecurityIdentifier(**doc.payload)
    fields = _mixin(
        stable_identifier=ident.cik, provider=provider, source_record_id=ident.cik,
        effective_at=None, run_id=doc.ingestion_run_id,
    )
    fields.update(cik=ident.cik, symbol=ident.symbol, name=ident.name)
    _upsert(db, models.SilverSecurityIdentifier, dict(provider=provider, cik=ident.cik), fields)
    db.commit()
    return True


def build_silver_filings(db: Session, cik: str, provider: str = "sec_edgar") -> int:
    doc = latest_raw_document(db, domain=DOMAIN, source_identifier=cik, document_type="filings")
    if doc is None:
        return 0
    n = 0
    for raw in doc.payload.get("filings", []):
        f = CompanyFiling(**raw)
        fields = _mixin(
            stable_identifier=f.cik, provider=provider, source_record_id=f.accession_number,
            effective_at=_naive(f.acceptance_datetime), run_id=doc.ingestion_run_id,
        )
        fields.update(
            cik=f.cik, symbol=f.symbol, form=f.form, accession_number=f.accession_number,
            filing_date=f.filing_date, acceptance_at=_naive(f.acceptance_datetime),
            report_date=f.report_date, primary_document=f.primary_document,
            primary_doc_description=f.primary_doc_description, is_amendment=f.is_amendment,
            source_url=f.source_url,
        )
        _upsert(
            db, models.SilverCompanyFiling,
            dict(provider=provider, accession_number=f.accession_number), fields,
        )
        n += 1
    db.commit()
    return n


def build_silver_material_events(db: Session, cik: str, provider: str = "sec_edgar") -> int:
    doc = latest_raw_document(
        db, domain=DOMAIN, source_identifier=cik, document_type="material_events"
    )
    if doc is None:
        return 0
    n = 0
    for raw in doc.payload.get("events", []):
        e = MaterialEvent(**raw)
        fields = _mixin(
            stable_identifier=e.cik, provider=provider, source_record_id=e.accession_number,
            effective_at=_naive(e.acceptance_datetime), run_id=doc.ingestion_run_id,
        )
        fields.update(
            cik=e.cik, symbol=e.symbol, accession_number=e.accession_number, form=e.form,
            filing_date=e.filing_date, acceptance_at=_naive(e.acceptance_datetime),
            items=e.items or None, is_amendment=e.is_amendment, source_url=e.source_url,
        )
        _upsert(
            db, models.SilverMaterialEvent,
            dict(provider=provider, accession_number=e.accession_number), fields,
        )
        n += 1
    db.commit()
    return n


def build_silver_facts(db: Session, cik: str, provider: str = "sec_edgar") -> int:
    doc = latest_raw_document(
        db, domain=DOMAIN, source_identifier=cik, document_type="company_facts"
    )
    if doc is None:
        return 0
    n = 0
    for raw in doc.payload.get("facts", []):
        fact = CompanyFact(**raw)
        warnings = None
        if fact.value is None:
            warnings = [{"type": "missing_value", "detail": "fact had no numeric value"}]
        fields = _mixin(
            stable_identifier=fact.cik, provider=provider,
            source_record_id=fact.accession_number, effective_at=None,
            run_id=doc.ingestion_run_id, warnings=warnings,
        )
        fields.update(
            cik=fact.cik, taxonomy=fact.taxonomy, concept=fact.concept, unit=fact.unit,
            value=fact.value, fiscal_year=fact.fiscal_year, fiscal_period=fact.fiscal_period,
            period_start=fact.period_start, period_end=fact.period_end, form=fact.form,
            filing_date=fact.filing_date, accession_number=fact.accession_number,
            is_amendment=fact.is_amendment, frame=fact.frame,
        )
        _upsert(
            db, models.SilverCompanyFact,
            dict(
                provider=provider, cik=fact.cik, accession_number=fact.accession_number,
                taxonomy=fact.taxonomy, concept=fact.concept, unit=fact.unit,
                period_start=fact.period_start, period_end=fact.period_end,
            ),
            fields,
        )
        n += 1
    db.commit()
    return n


def build_silver_all(db: Session, cik: str, provider: str = "sec_edgar") -> dict:
    return {
        "identifier": build_silver_identifier(db, cik, provider),
        "filings": build_silver_filings(db, cik, provider),
        "material_events": build_silver_material_events(db, cik, provider),
        "facts": build_silver_facts(db, cik, provider),
    }


def _upsert(db: Session, model, key: dict, fields: dict) -> None:
    existing = db.query(model).filter_by(**key).one_or_none()
    if existing is None:
        db.add(model(created_at=_now(), **fields))
    else:
        for k, v in fields.items():
            setattr(existing, k, v)


# --- Read helpers -------------------------------------------------------


def get_silver_identifier(db: Session, symbol: str, provider: str = "sec_edgar"):
    return (
        db.query(models.SilverSecurityIdentifier)
        .filter_by(provider=provider, symbol=symbol.upper())
        .one_or_none()
    )


def get_silver_filings(db: Session, cik: str, provider: str = "sec_edgar", form: str | None = None):
    query = db.query(models.SilverCompanyFiling).filter_by(provider=provider, cik=cik)
    if form:
        query = query.filter(models.SilverCompanyFiling.form == form)
    return query.order_by(models.SilverCompanyFiling.filing_date.desc()).all()


def get_active_facts(db: Session, cik: str, provider: str = "sec_edgar"):
    """The active value per (concept, unit, period): among all vintages
    (including amendments), the row with the latest filing_date wins. The
    originally-filed rows remain in the table untouched."""
    rows = (
        db.query(models.SilverCompanyFact)
        .filter_by(provider=provider, cik=cik)
        .all()
    )
    active: dict[tuple, models.SilverCompanyFact] = {}
    for r in rows:
        key = (r.concept, r.unit, r.period_start, r.period_end)
        current = active.get(key)
        if current is None or (r.filing_date or dt.date.min) > (current.filing_date or dt.date.min):
            active[key] = r
    return list(active.values())
