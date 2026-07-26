"""Fundamentals from SEC EDGAR Company Facts, shaped as a FundamentalsSnapshot.

This is the Yahoo-`.info` replacement for company fundamentals. SEC EDGAR is the
authoritative, public-domain source for the FINANCIAL-STATEMENT fields, but its
Company Facts are raw XBRL numbers - they do NOT contain sector, industry,
market cap, or P/E. This adapter derives everything it honestly can:

  - sector / industry : from the submissions feed's SIC code + description.
  - market_cap / trailing_pe : from a live price (the Webull->Twelve Data price
        chain) * SEC shares outstanding / SEC trailing EPS. Left null if no
        price is available (never fabricated).
  - margins / growth / ROE / free cash flow / debt / cash : derived from the
        latest annual (10-K) XBRL facts, with year-over-year for growth.
  - forward_pe / peg_ratio / ev_to_ebitda : left null - not derivable from SEC
        data alone (require forward estimates the SEC does not publish).

A missing concept yields a null field, never a zero or a guess. Failures raise
MarketDataError so the governed fundamentals cache + route handle them uniformly.
"""
from __future__ import annotations

import datetime as dt

from catalystiq.providers.base import DataDomain
from catalystiq.providers.fetch_tracker import record_fetch
from catalystiq.providers.market_data import MarketDataError
from catalystiq.schemas.fundamentals import CompanyFact
from catalystiq.schemas.market_data import FundamentalsSnapshot

# SIC major-group (first two digits) -> a SECTOR_ETF_MAP sector name. Coarse but
# honest: derived from the standard SIC division ranges, with the finance and
# transport/utilities/communications bands split where the 2-digit group is
# decisive. Anything unmapped leaves sector=None (industry still carries the
# verbatim SEC sicDescription).
def _sector_from_sic(sic: str | None) -> str | None:
    if not sic or not str(sic).strip().isdigit():
        return None
    code = int(str(sic).strip())
    mg = code // 100  # major group (first two digits)
    if mg == 13:
        return "Energy"  # oil & gas extraction (checked before the mining band)
    if mg in (10, 12, 14) or 1 <= mg <= 9:
        return "Basic Materials"  # metal/coal/nonmetallic mining, agriculture
    if 15 <= mg <= 17:
        return "Industrials"  # construction
    if mg == 28:
        return "Healthcare"  # chemicals & allied (pharma-heavy: 2833-2836)
    if mg in (35, 36, 38):
        return "Technology"  # computers/electronics/instruments
    if mg == 37:
        return "Consumer Cyclical"  # transportation equipment (autos)
    if 20 <= mg <= 39:
        return "Industrials"  # remaining manufacturing
    if mg == 48:
        return "Communication Services"  # communications
    if mg == 49:
        return "Utilities"
    if 40 <= mg <= 47:
        return "Industrials"  # transportation
    if 52 <= mg <= 59:
        return "Consumer Cyclical"  # retail
    if mg == 65 or code == 6798:
        return "Real Estate"  # real estate operators + REITs (6798)
    if 60 <= mg <= 67:
        return "Financial Services"
    if mg in (73, 78):
        return "Technology"  # business services (software 7372) / motion pictures
    if mg == 80:
        return "Healthcare"  # health services
    if 70 <= mg <= 89:
        return "Consumer Cyclical"  # remaining services
    return None


class SecEdgarFundamentalsProvider:
    """Serves get_fundamentals(symbol) -> FundamentalsSnapshot from SEC EDGAR.

    ``sec`` is a SecEdgarProvider (resolve_cik / get_company_profile /
    get_company_facts). ``price_provider`` is optional and only used to derive
    market_cap / trailing_pe from a live quote; when it is None or the quote
    fails, those two fields are left null (the SEC financial fields still fill)."""

    PROVIDER_NAME = "sec_edgar"
    DOMAIN = DataDomain.FUNDAMENTALS
    ADAPTER_VERSION = "1.0.0"

    def __init__(self, sec, price_provider=None) -> None:
        self._sec = sec
        self._price = price_provider

    def get_fundamentals(self, symbol: str) -> FundamentalsSnapshot:
        symbol = symbol.upper()
        try:
            ident = self._sec.resolve_cik(symbol)
            profile = self._sec.get_company_profile(ident.cik)
            facts = self._sec.get_company_facts(ident.cik)
        except MarketDataError:
            raise
        except Exception as exc:  # ProviderError etc. -> normalize for the cache/route
            raise MarketDataError(f"SEC EDGAR fundamentals for {symbol} failed: {exc}") from exc

        idx = _index_by_concept(facts)

        revenue, rev_fy = _latest_annual(idx, ("Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"))
        prev_revenue = _annual_for_year(idx, ("Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"),
            rev_fy - 1 if rev_fy else None)
        cost_of_rev, _ = _latest_annual(idx, ("CostOfRevenue", "CostOfGoodsAndServicesSold"))
        operating_income, _ = _latest_annual(idx, ("OperatingIncomeLoss",))
        net_income, ni_fy = _latest_annual(idx, ("NetIncomeLoss",))
        prev_net_income = _annual_for_year(idx, ("NetIncomeLoss",),
                                           ni_fy - 1 if ni_fy else None)
        equity = _latest_instant(idx, ("StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"))
        cash = _latest_instant(idx, ("CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"))
        long_term_debt = _latest_instant(idx, ("LongTermDebtNoncurrent", "LongTermDebt"))
        current_debt = _latest_instant(idx, ("DebtCurrent", "LongTermDebtCurrent"))
        op_cash_flow, _ = _latest_annual(idx, ("NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"))
        capex, _ = _latest_annual(idx, ("PaymentsToAcquirePropertyPlantAndEquipment",))
        eps, _ = _latest_annual(idx, ("EarningsPerShareDiluted", "EarningsPerShareBasic"))
        shares = _latest_instant(idx, ("EntityCommonStockSharesOutstanding",),
                                 taxonomies=("dei",))

        gross_margins = _safe_div(revenue - cost_of_rev, revenue) \
            if revenue is not None and cost_of_rev is not None else None
        operating_margins = _safe_div(operating_income, revenue) \
            if operating_income is not None and revenue is not None else None
        return_on_equity = _safe_div(net_income, equity) \
            if net_income is not None and equity else None
        free_cashflow = (op_cash_flow - capex) \
            if op_cash_flow is not None and capex is not None else None
        total_debt = _sum_present(long_term_debt, current_debt)
        revenue_growth = _growth(revenue, prev_revenue)
        earnings_growth = _growth(net_income, prev_net_income)

        # Price-derived fields (optional): market cap = price * shares,
        # trailing P/E = price / trailing EPS. Any failure leaves them null.
        market_cap = trailing_pe = None
        price = self._live_price(symbol)
        if price is not None:
            if shares:
                market_cap = price * shares
            if eps and eps > 0:
                trailing_pe = price / eps

        record_fetch(self.PROVIDER_NAME)
        return FundamentalsSnapshot(
            symbol=symbol,
            long_name=profile.get("name"),
            sector=_sector_from_sic(profile.get("sic")),
            industry=profile.get("sic_description"),
            market_cap=market_cap,
            trailing_pe=trailing_pe,
            forward_pe=None,      # not derivable from SEC data
            peg_ratio=None,       # not derivable from SEC data
            ev_to_ebitda=None,    # not derivable from SEC data
            revenue_growth=revenue_growth,
            earnings_growth=earnings_growth,
            gross_margins=gross_margins,
            operating_margins=operating_margins,
            return_on_equity=return_on_equity,
            free_cashflow=free_cashflow,
            total_debt=total_debt,
            total_cash=cash,
            as_of=dt.datetime.now(dt.timezone.utc),
        )

    def _live_price(self, symbol: str) -> float | None:
        if self._price is None:
            return None
        try:
            return float(self._price.get_quote(symbol).price)
        except Exception:  # price is best-effort; SEC financials still return
            return None


# --- XBRL fact selection helpers -------------------------------------------


def _index_by_concept(facts: list[CompanyFact]) -> dict[tuple[str, str], list[CompanyFact]]:
    idx: dict[tuple[str, str], list[CompanyFact]] = {}
    for f in facts:
        idx.setdefault((f.taxonomy, f.concept), []).append(f)
    return idx


def _matching(idx, concepts, taxonomies=("us-gaap",)) -> list[CompanyFact]:
    out: list[CompanyFact] = []
    for tax in taxonomies:
        for concept in concepts:
            out.extend(idx.get((tax, concept), []))
    return out


def _is_annual(f: CompanyFact) -> bool:
    # A full-year flow: 10-K form and (when present) an ~annual period span.
    if not (f.form or "").startswith("10-K"):
        return False
    if f.period_start and f.period_end:
        return (f.period_end - f.period_start).days >= 300
    return (f.fiscal_period or "") == "FY"


def _latest_annual(idx, concepts, taxonomies=("us-gaap",)) -> tuple[float | None, int | None]:
    """The most recent full-year value for the first concept that has one, plus
    its fiscal year (for year-over-year lookups)."""
    candidates = [f for f in _matching(idx, concepts, taxonomies)
                  if _is_annual(f) and f.value is not None]
    if not candidates:
        return None, None
    best = max(candidates, key=lambda f: (f.period_end or dt.date.min,
                                          f.filing_date or dt.date.min))
    return best.value, best.fiscal_year


def _annual_for_year(idx, concepts, fiscal_year, taxonomies=("us-gaap",)) -> float | None:
    if fiscal_year is None:
        return None
    candidates = [f for f in _matching(idx, concepts, taxonomies)
                  if _is_annual(f) and f.value is not None and f.fiscal_year == fiscal_year]
    if not candidates:
        return None
    best = max(candidates, key=lambda f: (f.period_end or dt.date.min,
                                          f.filing_date or dt.date.min))
    return best.value


def _latest_instant(idx, concepts, taxonomies=("us-gaap",)) -> float | None:
    """The most recent point-in-time (balance-sheet) value for the first
    concept that has one."""
    candidates = [f for f in _matching(idx, concepts, taxonomies) if f.value is not None]
    if not candidates:
        return None
    best = max(candidates, key=lambda f: (f.period_end or dt.date.min,
                                          f.filing_date or dt.date.min))
    return best.value


def _safe_div(numerator, denominator) -> float | None:
    if denominator in (None, 0):
        return None
    return numerator / denominator


def _growth(current, prior) -> float | None:
    if current is None or not prior:
        return None
    return (current - prior) / abs(prior)


def _sum_present(*values) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def get_sec_fundamentals_provider() -> SecEdgarFundamentalsProvider:
    """Build the SEC-EDGAR fundamentals adapter, with the scan price chain wired
    in for market-cap / P/E derivation. Raises MarketDataError when SEC_USER_AGENT
    is unset, so the governed cache + route surface it uniformly."""
    from catalystiq.config import get_settings
    from catalystiq.providers.fundamentals import SecEdgarProvider
    from catalystiq.providers.market_data import get_scan_market_data_provider

    settings = get_settings()
    try:
        sec = SecEdgarProvider(settings.sec_user_agent)
    except Exception as exc:
        raise MarketDataError(f"SEC EDGAR fundamentals unavailable: {exc}") from exc

    price_provider = None
    try:
        price_provider = get_scan_market_data_provider()
    except Exception:  # price is optional; SEC financials still work without it
        price_provider = None
    return SecEdgarFundamentalsProvider(sec, price_provider)
