"""SEC EDGAR -> FundamentalsSnapshot mapping: derives margins/growth/ROE/FCF/
debt/cash from XBRL Company Facts, sector/industry from the SIC code, and
market cap / trailing P/E from a live price * SEC shares/EPS. Missing inputs
yield null fields, never fabricated numbers. Offline - fake SEC + price."""
from __future__ import annotations

import datetime as dt

import pytest

from catalystiq.providers.market_data import MarketDataError
from catalystiq.providers.sec_fundamentals import (
    SecEdgarFundamentalsProvider,
    _sector_from_sic,
)
from catalystiq.schemas.fundamentals import CompanyFact, SecurityIdentifier
from catalystiq.schemas.market_data import Quote

NOW = dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc)


def _fact(concept, value, *, fy, taxonomy="us-gaap", unit="USD", annual=True):
    """A Company Fact. Annual (flow) facts get a 10-K form + a ~1y period;
    instant (balance-sheet) facts just carry a period_end."""
    return CompanyFact(
        cik="0000320193", taxonomy=taxonomy, concept=concept, unit=unit, value=value,
        fiscal_year=fy, fiscal_period="FY" if annual else None,
        period_start=dt.date(fy, 1, 1) if annual else None,
        period_end=dt.date(fy, 12, 31),
        form="10-K", filing_date=dt.date(fy + 1, 2, 1), accession_number="x",
        source="sec_edgar", retrieved_at=NOW,
    )


class _FakeSec:
    def __init__(self, facts, profile):
        self._facts = facts
        self._profile = profile

    def resolve_cik(self, symbol):
        return SecurityIdentifier(symbol=symbol.upper(), cik="0000320193",
                                  source="sec_edgar", retrieved_at=NOW)

    def get_company_profile(self, cik):
        return self._profile

    def get_company_facts(self, cik):
        return self._facts


class _FakePrice:
    def __init__(self, price=None, error=None):
        self._price = price
        self._error = error

    def get_quote(self, symbol):
        if self._error:
            raise self._error
        return Quote(symbol=symbol.upper(), price=self._price, as_of=NOW)


def _full_facts():
    return [
        _fact("Revenues", 400.0, fy=2025), _fact("Revenues", 320.0, fy=2024),
        _fact("CostOfRevenue", 250.0, fy=2025),
        _fact("OperatingIncomeLoss", 120.0, fy=2025),
        _fact("NetIncomeLoss", 100.0, fy=2025), _fact("NetIncomeLoss", 80.0, fy=2024),
        _fact("StockholdersEquity", 500.0, fy=2025, annual=False),
        _fact("CashAndCashEquivalentsAtCarryingValue", 60.0, fy=2025, annual=False),
        _fact("LongTermDebtNoncurrent", 90.0, fy=2025, annual=False),
        _fact("DebtCurrent", 10.0, fy=2025, annual=False),
        _fact("NetCashProvidedByUsedInOperatingActivities", 150.0, fy=2025),
        _fact("PaymentsToAcquirePropertyPlantAndEquipment", 30.0, fy=2025),
        _fact("EarningsPerShareDiluted", 5.0, fy=2025, unit="USD/shares"),
        _fact("EntityCommonStockSharesOutstanding", 20.0, fy=2025, taxonomy="dei",
              unit="shares", annual=False),
    ]


_PROFILE = {"name": "Apple Inc.", "sic": "3571", "sic_description": "Electronic Computers",
            "ticker": "AAPL"}


def test_derives_full_snapshot_with_price():
    provider = SecEdgarFundamentalsProvider(_FakeSec(_full_facts(), _PROFILE),
                                            _FakePrice(price=200.0))
    snap = provider.get_fundamentals("aapl")

    assert snap.symbol == "AAPL"
    assert snap.long_name == "Apple Inc."
    assert snap.sector == "Technology"  # SIC 3571 -> Technology
    assert snap.industry == "Electronic Computers"
    # Derived financials.
    assert snap.gross_margins == pytest.approx((400 - 250) / 400)
    assert snap.operating_margins == pytest.approx(120 / 400)
    assert snap.return_on_equity == pytest.approx(100 / 500)
    assert snap.revenue_growth == pytest.approx((400 - 320) / 320)
    assert snap.earnings_growth == pytest.approx((100 - 80) / 80)
    assert snap.free_cashflow == pytest.approx(150 - 30)
    assert snap.total_debt == pytest.approx(90 + 10)
    assert snap.total_cash == pytest.approx(60)
    # Price-derived: market cap = price * shares; trailing P/E = price / EPS.
    assert snap.market_cap == pytest.approx(200.0 * 20.0)
    assert snap.trailing_pe == pytest.approx(200.0 / 5.0)
    # Not derivable from SEC alone -> honest nulls.
    assert snap.forward_pe is None and snap.peg_ratio is None and snap.ev_to_ebitda is None


def test_without_price_leaves_market_cap_and_pe_null_but_keeps_financials():
    provider = SecEdgarFundamentalsProvider(_FakeSec(_full_facts(), _PROFILE), None)
    snap = provider.get_fundamentals("AAPL")
    assert snap.market_cap is None and snap.trailing_pe is None
    assert snap.gross_margins is not None and snap.total_cash == 60.0


def test_price_failure_is_best_effort_not_fatal():
    provider = SecEdgarFundamentalsProvider(
        _FakeSec(_full_facts(), _PROFILE), _FakePrice(error=MarketDataError("429")))
    snap = provider.get_fundamentals("AAPL")
    assert snap.market_cap is None and snap.trailing_pe is None
    assert snap.revenue_growth is not None  # SEC financials unaffected


def test_missing_concepts_yield_null_fields_never_zero():
    # Only revenue present: every other field is null (no crash, no zero-fill).
    facts = [_fact("Revenues", 400.0, fy=2025)]
    provider = SecEdgarFundamentalsProvider(_FakeSec(facts, _PROFILE), _FakePrice(price=10.0))
    snap = provider.get_fundamentals("AAPL")
    assert snap.gross_margins is None  # no CostOfRevenue
    assert snap.return_on_equity is None  # no equity
    assert snap.revenue_growth is None  # no prior year
    assert snap.free_cashflow is None
    assert snap.total_debt is None
    assert snap.market_cap is None  # no shares
    assert snap.trailing_pe is None  # no EPS


def test_negative_eps_does_not_produce_pe():
    facts = _full_facts() + [_fact("EarningsPerShareDiluted", -2.0, fy=2025, unit="USD/shares")]
    # The later-added negative EPS has the same period; loss companies shouldn't
    # get a positive/again-negative P/E - only positive EPS yields one.
    facts = [f for f in facts if f.concept != "EarningsPerShareDiluted"]
    facts.append(_fact("EarningsPerShareBasic", -2.0, fy=2025, unit="USD/shares"))
    provider = SecEdgarFundamentalsProvider(_FakeSec(facts, _PROFILE), _FakePrice(price=50.0))
    snap = provider.get_fundamentals("AAPL")
    assert snap.trailing_pe is None


def test_sec_failure_raises_marketdataerror():
    class _Boom:
        def resolve_cik(self, symbol):
            raise RuntimeError("no CIK for symbol")

    provider = SecEdgarFundamentalsProvider(_Boom(), None)
    with pytest.raises(MarketDataError):
        provider.get_fundamentals("ZZZZ")


def test_missing_sec_user_agent_factory_raises(monkeypatch):
    from catalystiq.providers import sec_fundamentals as sf

    class _S:
        sec_user_agent = ""

    monkeypatch.setattr("catalystiq.config.get_settings", lambda: _S())
    with pytest.raises(MarketDataError):
        sf.get_sec_fundamentals_provider()


@pytest.mark.parametrize("sic,expected", [
    ("3571", "Technology"),      # electronic computers
    ("2834", "Healthcare"),      # pharmaceutical preparations
    ("1311", "Energy"),          # crude petroleum & natural gas
    ("6021", "Financial Services"),  # national commercial banks
    ("6798", "Real Estate"),     # REITs
    ("4911", "Utilities"),       # electric services
    ("5411", "Consumer Cyclical"),   # grocery stores (retail)
    ("7372", "Technology"),      # prepackaged software
    ("", None),                  # unknown -> honest None
    (None, None),
])
def test_sector_from_sic(sic, expected):
    assert _sector_from_sic(sic) == expected
