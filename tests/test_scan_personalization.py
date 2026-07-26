"""Preference-aware personalization of the opportunity scan.

Root-cause regression coverage for the Trade Center returning the same four
names regardless of the submitted preferences. These tests prove, end to end,
that each preference reaches the backend and changes eligibility and/or ranking:

  * asset class filters the universe,
  * direction excludes disallowed setups,
  * risk tolerance + max acceptable loss gate volatility eligibility,
  * investment amount drives position sizing (never rejecting fractional stocks),
  * holding period re-ranks via horizon-appropriate factor weights,
  * the expensive scoring is cached ONCE per universe while each request is
    personalized independently (no cross-profile contamination),
  * a failed / empty personalized scan returns an honest empty/unavailable
    state - never a hard-coded fall back to a fixed list of names.
"""
from __future__ import annotations

import datetime as dt
import math

import pytest

import catalystiq.analysis.opportunity_score as osmod
from catalystiq.analysis.opportunity_score import (
    FACTOR_WEIGHTS,
    build_opportunity_score,
    clear_scan_cache,
    clear_scored_cache,
    scan_universe_personalized,
)
from catalystiq.analysis.personalize import personalize_scan, classify_direction
from catalystiq.schemas.market_data import OHLCVBar, Quote
from catalystiq.schemas.opportunity import (
    FactorScore,
    MlStatus,
    OpportunityScore,
    PersonalizationInfo,
    ScanPreferences,
)

NOW = dt.datetime(2026, 7, 18, 12, 0, tzinfo=dt.timezone.utc)
_LAST_CLOSED = dt.date(2026, 7, 17)

# The four names the Trade Center was previously stuck on - none may EVER appear
# as a fabricated fallback when a personalized scan fails or matches nothing.
_STUCK_FOUR = {"JPM", "AAPL", "BAC", "MA"}


# ---------------------------------------------------------------------------
# Bar fixtures (deterministic; ATR% probed empirically, see commit notes).
# ---------------------------------------------------------------------------
def _bizdays_ending(end: dt.date, n: int) -> list[dt.date]:
    days: list[dt.date] = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= dt.timedelta(days=1)
    return list(reversed(days))


def _series(n: int, base: float, trend: float, amp: float = 4.0) -> list[float]:
    return [base + trend * i + amp * math.sin(i / 5.0) for i in range(n)]


def _bars(dates, closes, hi: float, lo: float, vol: float = 2_000_000) -> list[OHLCVBar]:
    return [
        OHLCVBar(date=d, open=c, high=c * hi, low=c * lo, close=c, volume=vol)
        for d, c in zip(dates, closes)
    ]


# ATR% bands (empirically calibrated): CALM ~0.83, MODV ~4.36, WILD ~11.9,
# DOWN ~2.1 (downtrend). Prices chosen distinct so sizing/direction are visible.
_PROFILES: dict[str, dict] = {
    "CALM": dict(hi=1.004, lo=0.996, trend=0.25, base=100.0),   # atr ~0.83, up
    "MODV": dict(hi=1.022, lo=0.978, trend=0.25, base=250.0),   # atr ~4.36, up
    "WILD": dict(hi=1.060, lo=0.940, trend=0.25, base=100.0),   # atr ~11.9, up
    "DOWN": dict(hi=1.010, lo=0.990, trend=-0.25, base=180.0),  # atr ~2.1, down
}


class _FakeProvider:
    """Serves deterministic OHLCV per symbol from _PROFILES (SPY / sector ETFs
    get a calm uptrend). Never raises; never returns fundamentals."""

    def __init__(self, mapping: dict[str, str]):
        # mapping: candidate symbol -> profile key
        self._mapping = mapping

    def _profile_for(self, symbol: str) -> dict:
        key = self._mapping.get(symbol.upper())
        if key is not None:
            return _PROFILES[key]
        return dict(hi=1.010, lo=0.990, trend=0.10, base=100.0)  # SPY/ETF default

    def get_ohlcv(self, symbol, start, end=None, interval="1d"):
        p = self._profile_for(symbol)
        dates = _bizdays_ending(dt.date.today(), 300)
        return _bars(dates, _series(300, p["base"], p["trend"]), p["hi"], p["lo"])

    def get_quote(self, symbol):
        return Quote(symbol=symbol.upper(), price=100.0, previous_close=100.0,
                     as_of=dt.datetime.now(dt.timezone.utc))

    def get_fundamentals(self, symbol):
        raise AssertionError("scan must not fetch fundamentals")

    def get_news(self, symbol, limit=10):
        return []


class _ThrottledProvider:
    """Every fetch fails like an upstream 429 - a universe-wide data outage."""

    def get_ohlcv(self, symbol, start, end=None, interval="1d"):
        from catalystiq.providers.market_data import MarketDataError

        raise MarketDataError(f"Failed to fetch OHLCV for {symbol}: 429 Too Many Requests")

    def get_quote(self, symbol):
        return Quote(symbol=symbol.upper(), price=1.0, previous_close=1.0,
                     as_of=dt.datetime.now(dt.timezone.utc))

    def get_fundamentals(self, symbol):
        raise AssertionError("scan must not fetch fundamentals")

    def get_news(self, symbol, limit=10):
        return []


@pytest.fixture(autouse=True)
def _isolate_caches():
    # Also reset the shared market-data gate: a throttled-provider test trips the
    # per-provider rate-limit circuit-breaker cooldown, which would otherwise leak
    # into later tests (they'd short-circuit on the cooldown instead of the real
    # provider). Kept fully hermetic.
    from catalystiq.providers.market_data_gate import reset_market_data_gates

    clear_scan_cache()
    clear_scored_cache()
    reset_market_data_gates()
    yield
    clear_scan_cache()
    clear_scored_cache()
    reset_market_data_gates()


def _prefs(**kw) -> ScanPreferences:
    return ScanPreferences(**kw)


# ---------------------------------------------------------------------------
# Unit tests on the pure personalize layer (hand-built scores, full control).
# ---------------------------------------------------------------------------
def _factor(name: str, score: int, inputs: dict | None = None) -> FactorScore:
    return FactorScore(name=name, score=score, max_score=FACTOR_WEIGHTS[name],
                       status="available", inputs=inputs or {}, explanation="",
                       formula_version="opportunity_score_v1")


def _mk_score(symbol: str, *, trend=20, momentum=15, vol_liq=12, volr=9, mkt=6,
              close=100.0, atr_pct=2.0, rvol=20.0, direction="long") -> OpportunityScore:
    if direction == "long":
        tin = {"close": close, "price_vs_sma_50_pct": 5.0,
               "sma_20": close * 0.99, "sma_50": close * 0.97}
    else:
        tin = {"close": close, "price_vs_sma_50_pct": -5.0,
               "sma_20": close * 0.97, "sma_50": close * 0.99}
    factors = [
        _factor("trend", trend, tin),
        _factor("momentum", momentum),
        _factor("volume_liquidity", vol_liq),
        _factor("volatility_risk", volr,
                {"atr_14_pct": atr_pct, "realized_volatility_20d_annualized_pct": rvol}),
        _factor("market_sector", mkt),
    ]
    total = trend + momentum + vol_liq + volr + mkt
    return OpportunityScore(
        symbol=symbol, status="available", score_type="rule_based", score=total,
        max_score=100, label="x", formula_version="opportunity_score_v1",
        calculated_at=NOW, data_as_of=NOW, freshness="current", factor_coverage="5/5",
        factors=factors, unavailable_factors=[], warnings=[],
        ml=MlStatus(status="not_available", reason=""),
    )


def test_asset_class_filters_universe():
    stock = _mk_score("AAPL")   # governed stock
    etf = _mk_score("SPY")      # governed ETF
    scored = [stock, etf]

    only_stocks = personalize_scan(scored, _prefs(assets=["Stocks"]),
                                   now=NOW, universe_size=2, top=4)
    assert [c.symbol for c in only_stocks.candidates] == ["AAPL"]

    only_etfs = personalize_scan(scored, _prefs(assets=["ETFs"]),
                                 now=NOW, universe_size=2, top=4)
    assert [c.symbol for c in only_etfs.candidates] == ["SPY"]

    # An unsupported-only selection (options) matches nothing in a stock/ETF
    # universe -> honest empty set, never a silent default to stocks.
    empty = personalize_scan(scored, _prefs(assets=["Options"]),
                             now=NOW, universe_size=2, top=4)
    assert empty.candidates == []
    assert empty.status == "ok"


def test_direction_excludes_disallowed_setups():
    up = _mk_score("AAPL", direction="long")
    down = _mk_score("XOM", direction="short")
    assert classify_direction(up) == "long"
    assert classify_direction(down) == "short"
    scored = [up, down]

    long_only = personalize_scan(scored, _prefs(direction="long"),
                                 now=NOW, universe_size=2, top=4)
    got = [c.symbol for c in long_only.candidates]
    assert "XOM" not in got and got == ["AAPL"]

    both = personalize_scan(scored, _prefs(direction="both"),
                            now=NOW, universe_size=2, top=4)
    assert "XOM" in [c.symbol for c in both.candidates]
    # The short candidate is tagged short in its personalization block.
    xom = next(c for c in both.candidates if c.symbol == "XOM")
    assert xom.personalization.direction == "short"


def test_risk_tolerance_and_max_loss_gate_volatility():
    calm = _mk_score("AAPL", atr_pct=0.8)
    modv = _mk_score("JPM", atr_pct=4.4)
    scored = [calm, modv]

    # Conservative ceiling (3.5% ATR) drops the 4.4% name on risk grounds.
    conservative = personalize_scan(scored, _prefs(risk="conservative", style="swing",
                                                   max_loss_pct=10), now=NOW,
                                    universe_size=2, top=4)
    assert [c.symbol for c in conservative.candidates] == ["AAPL"]

    # Aggressive tolerates it on risk, and a generous max loss clears the
    # style-scaled stop (1.5 * 4.4 = 6.6% < 10%).
    aggressive = personalize_scan(scored, _prefs(risk="aggressive", style="swing",
                                                 max_loss_pct=10), now=NOW,
                                  universe_size=2, top=4)
    assert set(c.symbol for c in aggressive.candidates) == {"AAPL", "JPM"}

    # A tight max loss re-excludes the volatile name even for an aggressive
    # trader: 1.5 * 4.4 = 6.6% stop cannot fit a 5% budget.
    tight = personalize_scan(scored, _prefs(risk="aggressive", style="swing",
                                            max_loss_pct=5), now=NOW,
                             universe_size=2, top=4)
    assert [c.symbol for c in tight.candidates] == ["AAPL"]


def test_investment_amount_sizes_position_and_never_rejects_fractional():
    # A high-priced stock is NOT rejected when fractional shares are supported.
    pricey = _mk_score("BRK", close=600.0, atr_pct=1.0)
    small = personalize_scan([pricey], _prefs(amount=100, fractional_shares=True,
                                              max_loss_pct=10), now=NOW,
                             universe_size=1, top=4)
    assert [c.symbol for c in small.candidates] == ["BRK"]
    info_small = small.candidates[0].personalization
    assert info_small.est_shares is not None and 0 < info_small.est_shares < 1

    # Larger capital -> proportionally larger position (sizing actually uses it).
    big = personalize_scan([pricey], _prefs(amount=100_000, fractional_shares=True,
                                            max_loss_pct=10), now=NOW,
                           universe_size=1, top=4)
    info_big = big.candidates[0].personalization
    assert info_big.est_position_value > info_small.est_position_value

    # Without fractional support, capital too small for one whole share IS
    # rejected (honest affordability), not silently kept.
    no_frac = personalize_scan([pricey], _prefs(amount=100, fractional_shares=False,
                                                max_loss_pct=10), now=NOW,
                               universe_size=1, top=4)
    assert no_frac.candidates == []


def test_holding_period_reranks_by_horizon_weights():
    # TREND leads on trend, MOMO leads on momentum. Long-term weighting favors
    # trend; intraday weighting favors momentum -> the winner flips.
    trend_name = _mk_score("TREND", trend=30, momentum=5, vol_liq=10, volr=9, mkt=5)
    momo_name = _mk_score("MOMO", trend=10, momentum=25, vol_liq=10, volr=9, mkt=5)
    scored = [trend_name, momo_name]

    long_rank = personalize_scan(scored, _prefs(style="long", max_loss_pct=20),
                                 now=NOW, universe_size=2, top=4)
    assert [c.symbol for c in long_rank.candidates][0] == "TREND"

    intraday_rank = personalize_scan(scored, _prefs(style="intraday", max_loss_pct=20),
                                     now=NOW, universe_size=2, top=4)
    assert [c.symbol for c in intraday_rank.candidates][0] == "MOMO"


def test_swing_weighting_reproduces_base_ranking():
    # Swing weights == base FACTOR_WEIGHTS, so the personalized score equals the
    # generic rule-based score (a useful invariant).
    s = _mk_score("AAPL", trend=24, momentum=18, vol_liq=14, volr=10, mkt=7)
    out = personalize_scan([s], _prefs(style="swing", max_loss_pct=20),
                           now=NOW, universe_size=1, top=4)
    info = out.candidates[0].personalization
    assert info.personalized_score == info.base_score == 73


def test_empty_match_is_honest_not_a_fallback():
    # Nothing clears an impossibly tight max loss -> empty "ok" state with a
    # preferences-aware note, and NONE of the previously-stuck four names.
    scored = [_mk_score("AAPL", atr_pct=2.0), _mk_score("JPM", atr_pct=3.0),
              _mk_score("BAC", atr_pct=2.5), _mk_score("MA", atr_pct=2.2)]
    out = personalize_scan(scored, _prefs(max_loss_pct=0.01), now=NOW,
                           universe_size=4, top=4)
    assert out.candidates == []
    assert out.status == "ok"
    assert out.note and "preferences" in out.note.lower()
    assert not (_STUCK_FOUR & {c.symbol for c in out.candidates})


def test_data_outage_propagates_as_unavailable():
    out = personalize_scan([], _prefs(), now=NOW, universe_size=24, top=4,
                           data_outage=True, outage_note="rate-limited")
    assert out.status == "unavailable"
    assert out.candidates == []


# ---------------------------------------------------------------------------
# End-to-end through the scan orchestrator (real scoring pipeline + DB).
# ---------------------------------------------------------------------------
def test_e2e_risk_tolerance_changes_eligible_set(test_db_session):
    # AAPL calm (atr ~0.83), JPM moderately volatile (atr ~4.36); both uptrend,
    # both governed sectors so no fundamentals fetch.
    provider = _FakeProvider({"AAPL": "CALM", "JPM": "MODV"})

    conservative = scan_universe_personalized(
        provider, test_db_session, NOW,
        _prefs(risk="conservative", style="swing", max_loss_pct=10, direction="long"),
        top=4, universe=["AAPL", "JPM"],
    )
    assert [c.symbol for c in conservative.candidates] == ["AAPL"]

    clear_scored_cache()  # force a fresh scoring pass for the second provider view
    aggressive = scan_universe_personalized(
        provider, test_db_session, NOW,
        _prefs(risk="aggressive", style="swing", max_loss_pct=10, direction="long"),
        top=4, universe=["AAPL", "JPM"],
    )
    assert set(c.symbol for c in aggressive.candidates) == {"AAPL", "JPM"}
    # Every candidate carries auditable personalization, never a bare score.
    for c in aggressive.candidates:
        assert c.personalization is not None
        assert c.personalization.asset_class == "stock"


def test_e2e_direction_filter_excludes_shorts(test_db_session):
    provider = _FakeProvider({"AAPL": "CALM", "XOM": "DOWN"})

    long_only = scan_universe_personalized(
        provider, test_db_session, NOW,
        _prefs(direction="long", risk="moderate", style="swing", max_loss_pct=10),
        top=4, universe=["AAPL", "XOM"],
    )
    assert "XOM" not in [c.symbol for c in long_only.candidates]

    clear_scored_cache()
    both = scan_universe_personalized(
        provider, test_db_session, NOW,
        _prefs(direction="both", risk="moderate", style="swing", max_loss_pct=10),
        top=4, universe=["AAPL", "XOM"],
    )
    assert "XOM" in [c.symbol for c in both.candidates]


def test_failed_personalized_scan_returns_no_fallback_symbols(test_db_session):
    out = scan_universe_personalized(
        _ThrottledProvider(), test_db_session, NOW,
        _prefs(risk="moderate", style="swing"), top=4, universe=["AAPL", "JPM", "BAC", "MA"],
    )
    assert out.candidates == []
    assert out.status == "unavailable"
    assert not (_STUCK_FOUR & {c.symbol for c in out.candidates})


# ---------------------------------------------------------------------------
# The scored universe is scored ONCE and personalized per request (cache
# separation + invalidation), and the endpoint maps preference query params.
#
# These run at the orchestrator/router-helper level (no TestClient) on purpose:
# the app's TestClient lifespan starts the background universe warmer, whose real
# network calls pollute global gate state and make cross-test behavior flaky.
# ---------------------------------------------------------------------------
def test_personalized_cached_reuses_scored_across_profiles(test_db_session, monkeypatch):
    # Two different profiles over the SAME universe are personalized independently
    # from ONE cached scored set: the expensive scoring runs once, yet each
    # profile gets its own filtered/ranked result (no cross-profile contamination,
    # and a new profile is never served the previous profile's candidates).
    calls = {"n": 0}
    original = osmod._score_universe

    def counting(*a, **k):
        calls["n"] += 1
        return original(*a, **k)

    monkeypatch.setattr(osmod, "_score_universe", counting)

    provider = _FakeProvider({"AAPL": "CALM", "JPM": "MODV"})
    a = osmod.scan_universe_personalized_cached(
        provider, test_db_session, NOW,
        _prefs(risk="conservative", style="swing", max_loss_pct=10, direction="long"),
        top=4, universe=["AAPL", "JPM"],
    )
    b = osmod.scan_universe_personalized_cached(
        provider, test_db_session, NOW,
        _prefs(risk="aggressive", style="swing", max_loss_pct=10, direction="long"),
        top=4, universe=["AAPL", "JPM"],
    )
    assert [c.symbol for c in a.candidates] == ["AAPL"]
    assert set(c.symbol for c in b.candidates) == {"AAPL", "JPM"}
    assert calls["n"] == 1  # scored universe computed once, reused for both


def test_resolve_scan_preferences_maps_query_params():
    # The backend RECEIVES and maps the exact submitted values; unrecognized /
    # absent input falls back to defaults, and no preference param at all means a
    # generic (non-personalized) scan.
    from catalystiq.routers.analysis import _resolve_scan_preferences

    assert _resolve_scan_preferences(None, None, None, None, None, None, None, None) is None

    p = _resolve_scan_preferences(
        "intraday", "aggressive", 2000.0, 1.0, "both", "stocks,etfs", True, "no leverage"
    )
    assert p is not None
    assert p.style == "intraday"
    assert p.risk == "aggressive"
    assert p.amount == 2000.0
    assert p.max_loss_pct == 1.0
    assert p.direction == "both"
    assert set(p.assets) == {"stock", "etf"}
    assert p.fractional_shares is True
    assert p.constraints == "no leverage"

    # A single preference param is enough to turn on personalization; the rest
    # default. Invalid enum values fall back rather than erroring.
    q = _resolve_scan_preferences("nonsense", None, None, None, None, None, None, None)
    assert q is not None and q.style == "swing"  # default
