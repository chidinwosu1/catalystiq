"""Normalized SEC EDGAR shapes (§6). Provider-agnostic fundamentals: a
security identifier (ticker<->CIK), company filings, XBRL company facts, and
8-K material events. Every financial fact retains its full provenance (CIK,
taxonomy, concept, unit, fiscal period, filing form/date, accession number,
amendment status, source URL) so it can be traced and a revision distinguished
from the originally-filed value.
"""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class SecurityIdentifier(BaseModel):
    symbol: str
    cik: str  # 10-digit zero-padded
    name: str | None = None
    source: str
    retrieved_at: dt.datetime


class CompanyFiling(BaseModel):
    cik: str
    symbol: str | None = None
    form: str
    accession_number: str
    filing_date: dt.date | None = None
    acceptance_datetime: dt.datetime | None = None
    report_date: dt.date | None = None
    primary_document: str | None = None
    primary_doc_description: str | None = None
    is_amendment: bool = False
    source_url: str | None = None
    source: str
    retrieved_at: dt.datetime


class CompanyFact(BaseModel):
    """One XBRL fact - doubles as the financial-statement fact (§6): an XBRL
    company fact IS a statement-line fact, so it isn't duplicated into a
    separate shape."""

    cik: str
    taxonomy: str  # e.g. "us-gaap", "dei"
    concept: str  # e.g. "Revenues"
    unit: str  # e.g. "USD", "shares"
    value: float | None = None
    fiscal_year: int | None = None
    fiscal_period: str | None = None  # FY, Q1..Q4
    period_start: dt.date | None = None
    period_end: dt.date | None = None
    form: str | None = None
    filing_date: dt.date | None = None
    accession_number: str | None = None
    is_amendment: bool = False
    frame: str | None = None
    source: str
    retrieved_at: dt.datetime


class MaterialEvent(BaseModel):
    """An 8-K (material event) filing."""

    cik: str
    symbol: str | None = None
    accession_number: str
    form: str
    filing_date: dt.date | None = None
    acceptance_datetime: dt.datetime | None = None
    items: list[str] = []
    is_amendment: bool = False
    source_url: str | None = None
    source: str
    retrieved_at: dt.datetime
