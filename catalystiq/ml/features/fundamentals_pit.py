"""Point-in-time SEC fundamentals features (vintage- and amendment-correct).

SEC XBRL company facts are genuinely point-in-time: every filing (including an
amendment, e.g. 10-K/A) lands as its own ``SilverCompanyFact`` row carrying the
real ``filing_date`` on which it became public, and the original filing's rows
are never overwritten. That lets us reconstruct exactly what was known on a
historical prediction date:

  * A fact is only eligible if ``filing_date <= as_of`` (the last closed
    session at/before the prediction timestamp). A restatement filed AFTER the
    prediction date is invisible - we never use a revised value that did not
    exist yet.
  * Among the eligible vintages of one (concept, unit, period), the one with
    the LATEST ``filing_date`` wins - so an amendment that WAS public by the
    prediction date correctly supersedes the original, while one filed later
    does not.

Every feature carries its own provenance: ``source_provider='sec_edgar'``,
``source_event_timestamp`` = the reporting period end, and
``available_at_timestamp`` = the governing filing date (the true release).

Fail-closed: when the CIK can't be resolved, or the required facts/periods are
not present as of the date, the feature is emitted MISSING (never fabricated,
never silently substituted with a current value).
"""
from __future__ import annotations

import datetime as dt

from catalystiq.ml.features.schema import DataQualityStatus, PointInTimeFeature

SEC_PROVIDER = "sec_edgar"

# XBRL concept fallbacks (filers tag revenue/cost under different concepts).
REVENUE_CONCEPTS = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
)
COST_CONCEPTS = (
    "CostOfRevenue",
    "CostOfGoodsAndServicesSold",
    "CostOfGoodsSold",
)

# recent_filing_event look-back window (calendar days before the prediction).
RECENT_FILING_WINDOW_DAYS = 14


def _active_facts_asof(db, cik: str, as_of: dt.date):
    """Latest-filing-date fact per (concept, unit, period) among facts whose
    ``filing_date <= as_of``. This is the point-in-time analogue of the
    pipeline's get_active_facts (which has no date bound)."""
    from catalystiq.db import models

    rows = (
        db.query(models.SilverCompanyFact)
        .filter(
            models.SilverCompanyFact.provider == SEC_PROVIDER,
            models.SilverCompanyFact.cik == cik,
            models.SilverCompanyFact.filing_date != None,  # noqa: E711
            models.SilverCompanyFact.filing_date <= as_of,
        )
        .all()
    )
    active: dict[tuple, object] = {}
    for r in rows:
        key = (r.concept, r.unit, r.period_start, r.period_end)
        cur = active.get(key)
        if cur is None or (r.filing_date or dt.date.min) > (cur.filing_date or dt.date.min):
            active[key] = r
    return list(active.values())


def _annual(facts, concepts):
    """Annual (FY, ~1yr span) facts for the given concepts, value present."""
    out = []
    for f in facts:
        if f.concept not in concepts or f.value is None:
            continue
        if f.period_start and f.period_end:
            span = (f.period_end - f.period_start).days
            is_annual = 330 <= span <= 400
        else:
            is_annual = (f.fiscal_period == "FY")
        if is_annual or f.fiscal_period == "FY":
            out.append(f)
    return out


def _pick_first(facts, concepts):
    """Facts for the first matching concept (respecting the fallback order)."""
    for concept in concepts:
        matches = [f for f in facts if f.concept == concept and f.value is not None]
        if matches:
            return matches
    return []


def pit_fundamental_features(
    db,
    symbol: str,
    prediction_timestamp: dt.datetime,
    *,
    as_of: dt.date,
    retrieved_at: dt.datetime,
) -> list[PointInTimeFeature]:
    """Return the SEC fundamentals features (revenue YoY, gross margin, recent
    filing event) with point-in-time provenance. ``as_of`` is the last closed
    session date used everywhere else in the provider."""
    names = ("pit_revenue_yoy", "pit_gross_margin", "recent_filing_event")

    def missing(name: str) -> PointInTimeFeature:
        return _feature(symbol, prediction_timestamp, name, None,
                        event=prediction_timestamp, available=prediction_timestamp,
                        retrieved=retrieved_at, status=DataQualityStatus.MISSING)

    if db is None:
        return [missing(n) for n in names]

    from catalystiq.pipelines.fundamentals_pipeline import get_silver_identifier
    from catalystiq.db import models

    ident = get_silver_identifier(db, symbol, provider=SEC_PROVIDER)
    if ident is None:
        return [missing(n) for n in names]
    cik = ident.cik

    facts = _active_facts_asof(db, cik, as_of)

    out: list[PointInTimeFeature] = []
    out.append(_revenue_yoy_feature(symbol, prediction_timestamp, retrieved_at, facts) or missing("pit_revenue_yoy"))
    out.append(_gross_margin_feature(symbol, prediction_timestamp, retrieved_at, facts) or missing("pit_gross_margin"))
    out.append(_recent_filing_feature(db, cik, symbol, prediction_timestamp, retrieved_at, as_of))
    return out


def _revenue_yoy_feature(symbol, prediction_timestamp, retrieved_at, facts):
    rev = _annual(_pick_first(facts, REVENUE_CONCEPTS), REVENUE_CONCEPTS)
    if len(rev) < 2:
        return None
    rev.sort(key=lambda f: f.period_end or dt.date.min, reverse=True)
    current = rev[0]
    target_prev_end = (current.period_end - dt.timedelta(days=365)) if current.period_end else None
    prior = None
    for f in rev[1:]:
        if f.period_end and target_prev_end and abs((f.period_end - target_prev_end).days) <= 45:
            prior = f
            break
    if prior is None or not prior.value:
        return None
    yoy = (current.value - prior.value) / abs(prior.value)
    available = _governing_filing_dt(current, prior, prediction_timestamp)
    event = _period_end_dt(current, prediction_timestamp)
    return _feature(symbol, prediction_timestamp, "pit_revenue_yoy", float(yoy),
                    event=event, available=available, retrieved=retrieved_at)


def _gross_margin_feature(symbol, prediction_timestamp, retrieved_at, facts):
    rev_by = _by_period(_pick_first(facts, REVENUE_CONCEPTS))
    cost_by = _by_period(_pick_first(facts, COST_CONCEPTS))
    common = [p for p in rev_by if p in cost_by]
    if not common:
        return None
    common.sort(key=lambda p: p[1] or dt.date.min, reverse=True)
    period = common[0]
    rev_f, cost_f = rev_by[period], cost_by[period]
    if not rev_f.value:
        return None
    gm = (rev_f.value - cost_f.value) / abs(rev_f.value)
    available = _governing_filing_dt(rev_f, cost_f, prediction_timestamp)
    event = _period_end_dt(rev_f, prediction_timestamp)
    return _feature(symbol, prediction_timestamp, "pit_gross_margin", float(gm),
                    event=event, available=available, retrieved=retrieved_at)


def _recent_filing_feature(db, cik, symbol, prediction_timestamp, retrieved_at, as_of):
    """1 if a filing (10-Q/10-K/8-K or amendment) became public within the
    look-back window ending at ``as_of``; 0 otherwise. 0 is a legitimate value
    (we successfully looked and found none), never MISSING."""
    from catalystiq.db import models

    window_start = as_of - dt.timedelta(days=RECENT_FILING_WINDOW_DAYS)
    filing = (
        db.query(models.SilverCompanyFiling)
        .filter(
            models.SilverCompanyFiling.provider == SEC_PROVIDER,
            models.SilverCompanyFiling.cik == cik,
            models.SilverCompanyFiling.filing_date != None,  # noqa: E711
            models.SilverCompanyFiling.filing_date <= as_of,
            models.SilverCompanyFiling.filing_date >= window_start,
        )
        .order_by(models.SilverCompanyFiling.filing_date.desc())
        .first()
    )
    event_dt = prediction_timestamp
    available = prediction_timestamp
    if filing is not None and filing.filing_date is not None:
        available = _to_dt(filing.filing_date, prediction_timestamp)
        event_dt = available
    return _feature(symbol, prediction_timestamp, "recent_filing_event",
                    1.0 if filing is not None else 0.0,
                    event=event_dt, available=available, retrieved=retrieved_at)


# --- small helpers ----------------------------------------------------------
def _by_period(facts) -> dict:
    out: dict = {}
    for f in facts:
        key = (f.period_start, f.period_end)
        cur = out.get(key)
        if cur is None or (f.filing_date or dt.date.min) > (cur.filing_date or dt.date.min):
            out[key] = f
    return out


def _to_dt(d: dt.date, ref: dt.datetime) -> dt.datetime:
    return dt.datetime.combine(d, dt.time(), tzinfo=ref.tzinfo)


def _period_end_dt(fact, ref: dt.datetime) -> dt.datetime:
    if getattr(fact, "period_end", None):
        return _to_dt(fact.period_end, ref)
    return ref


def _governing_filing_dt(*facts_and_ref):
    *facts, ref = facts_and_ref
    dates = [f.filing_date for f in facts if getattr(f, "filing_date", None)]
    if not dates:
        return ref
    return _to_dt(max(dates), ref)


def _feature(symbol, prediction_timestamp, name, value, *, event, available, retrieved, status=None):
    if status is None:
        status = DataQualityStatus.OK if value is not None else DataQualityStatus.MISSING
    # Never let provenance violate the schema ordering.
    if available > prediction_timestamp:
        available = prediction_timestamp
    if event > available:
        event = available
    return PointInTimeFeature(
        symbol=symbol.upper(),
        prediction_timestamp=prediction_timestamp,
        feature_name=name,
        feature_value=value,
        source_provider=SEC_PROVIDER,
        source_event_timestamp=event,
        available_at_timestamp=available,
        retrieved_at_timestamp=retrieved,
        data_quality_status=status,
    )
