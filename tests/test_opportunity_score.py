"""Rule-Based Opportunity Score: deterministic factor math, strict
insufficient-data handling, candle-close exclusion, and the versioned contract.
All offline (synthetic bars, injected clock)."""
from __future__ import annotations

import ast
import datetime as dt
import json
import math
import pathlib

from catalystiq.analysis.opportunity_score import (
    FACTOR_WEIGHTS,
    FORMULA_VERSION,
    build_opportunity_score,
)
from catalystiq.schemas.market_data import OHLCVBar

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
# Saturday: the most recent closed NYSE session is Fri 2026-07-17.
_NOW = dt.datetime(2026, 7, 18, 12, 0, tzinfo=dt.timezone.utc)
_LAST_CLOSED = dt.date(2026, 7, 17)


def _bizdays_ending(end: dt.date, n: int) -> list[dt.date]:
    days: list[dt.date] = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= dt.timedelta(days=1)
    return list(reversed(days))


def _bars(dates: list[dt.date], closes: list[float], volume: float = 2_000_000) -> list[OHLCVBar]:
    out = []
    for d, c in zip(dates, closes):
        out.append(OHLCVBar(date=d, open=c, high=c * 1.01, low=c * 0.99, close=c, volume=volume))
    return out


def _rising(n: int, base: float, trend: float, amp: float) -> list[float]:
    # Zig-zag uptrend so swing highs/lows form (needed for trend_structure).
    return [base + trend * i + amp * math.sin(i / 5.0) for i in range(n)]


def _dataset(n: int = 260):
    dates = _bizdays_ending(_LAST_CLOSED, n)
    sym = _bars(dates, _rising(n, 100, 0.30, 5))
    spy = _bars(dates, _rising(n, 100, 0.10, 3))
    sector = _bars(dates, _rising(n, 100, 0.15, 3))
    return sym, spy, sector


def _score(sym, spy=None, sector=None, now=_NOW):
    return build_opportunity_score(
        "TEST", sym, now=now, market_bars=spy, market_symbol="SPY",
        sector_bars=sector, sector_symbol="XLK",
    )


# --- Static contract ----------------------------------------------------


def test_factor_weights_total_100():
    assert sum(FACTOR_WEIGHTS.values()) == 100


def test_bands_and_ml_and_exposure_fields():
    sym, spy, sector = _dataset()
    r = _score(sym, spy, sector)
    assert r.status == "available"
    assert r.score_type == "rule_based"
    assert r.formula_version == FORMULA_VERSION
    # Every score exposes formula, factors, freshness, timestamps, coverage.
    assert r.calculated_at is not None and r.data_as_of is not None
    assert r.freshness == "current"
    assert r.factor_coverage == "5/5"
    for f in r.factors:
        assert f.formula_version == FORMULA_VERSION
        assert f.max_score == FACTOR_WEIGHTS[f.name]
        assert set(("name", "score", "max_score", "status", "inputs", "explanation")).issubset(
            f.model_dump().keys()
        )
    # ML fields explicitly unavailable; rule score never labeled probability/AI.
    assert r.ml.status == "not_available"
    blob = json.dumps(r.model_dump(mode="json")).lower()
    assert "probability" not in blob
    assert "ai confidence" not in blob and "ml prediction" not in blob


def test_score_within_bounds_and_contributions_sum_exactly():
    sym, spy, sector = _dataset()
    r = _score(sym, spy, sector)
    assert 0 <= r.score <= 100
    assert r.label in {
        "Strong setup", "Favorable setup", "Mixed / Watch", "Weak setup", "Unfavorable setup",
    }
    # Factor contributions sum EXACTLY to the final score.
    assert sum(f.score for f in r.factors) == r.score


def test_behavioral_and_sentiment_always_unavailable():
    sym, spy, sector = _dataset()
    r = _score(sym, spy, sector)
    names = {u.name: u.reason for u in r.unavailable_factors}
    assert names.get("behavioral") == "No validated data source"
    assert names.get("sentiment") == "No validated data source"


# --- Insufficient-data handling ----------------------------------------


def test_short_history_is_insufficient_not_a_bearish_zero():
    sym, spy, sector = _dataset(n=30)  # too few bars for SMA50/MACD/etc.
    r = _score(sym, spy, sector)
    assert r.status == "insufficient_data"
    # Crucially: NO numeric total is emitted (missing != bearish zero).
    assert r.score is None
    assert r.label is None
    # At least one required factor is flagged insufficient (not scored 0).
    assert any(f.status == "insufficient_data" for f in r.factors)


def test_missing_market_sector_is_insufficient_not_renormalized():
    sym, _, _ = _dataset()
    r = _score(sym, spy=None, sector=None)  # no benchmark/sector
    assert r.status == "insufficient_data"
    assert r.score is None  # not renormalized to 100 on the four core factors
    ms = [f for f in r.factors if f.name == "market_sector"][0]
    assert ms.status == "insufficient_data"


def test_stale_data_cannot_produce_a_current_score():
    sym, spy, sector = _dataset()
    later = dt.datetime(2026, 7, 24, 12, 0, tzinfo=dt.timezone.utc)  # a week on
    r = _score(sym, spy, sector, now=later)
    assert r.status == "insufficient_data"
    assert r.freshness == "stale"
    assert r.score is None


def test_unclosed_candle_is_excluded():
    sym, spy, sector = _dataset()
    # Score "as of" Monday mid-session; append an in-progress Monday bar.
    monday = dt.date(2026, 7, 20)
    partial = OHLCVBar(date=monday, open=999, high=999, low=999, close=999, volume=1)
    now_mon = dt.datetime(2026, 7, 20, 12, 0, tzinfo=dt.timezone.utc)  # before the close
    r = _score(sym + [partial], spy, sector, now=now_mon)
    # The partial Monday candle is dropped; data_as_of stays at Friday's close.
    assert r.data_as_of.date() == _LAST_CLOSED


# --- Isolation: FRED cannot enter the score ----------------------------


def test_endpoint_returns_valid_contract(client, test_db_session):
    from catalystiq.main import app
    from catalystiq.providers.market_data import get_market_data_provider
    from catalystiq.schemas.market_data import FundamentalsSnapshot, Quote

    today = dt.date.today()

    class _FakeProvider:
        def get_ohlcv(self, symbol, start, end=None, interval="1d"):
            dates = _bizdays_ending(today, 300)
            return _bars(dates, _rising(300, 100, 0.25, 4))

        def get_quote(self, symbol):
            return Quote(symbol=symbol.upper(), price=175.0, previous_close=174.0,
                         as_of=dt.datetime.now(dt.timezone.utc))

        def get_fundamentals(self, symbol):
            return FundamentalsSnapshot(symbol=symbol.upper(), sector="Technology",
                                        as_of=dt.datetime.now(dt.timezone.utc))

        def get_news(self, symbol, limit=10):
            return []

    app.dependency_overrides[get_market_data_provider] = lambda: _FakeProvider()
    try:
        r = client.get("/analysis/NVDA/opportunity-score")
    finally:
        del app.dependency_overrides[get_market_data_provider]
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "NVDA"
    assert body["score_type"] == "rule_based"
    assert body["max_score"] == 100
    assert body["formula_version"] == "opportunity_score_v1"
    assert body["ml"]["status"] == "not_available"
    assert body["status"] in ("available", "insufficient_data")
    assert "probability" not in json.dumps(body).lower()


def test_scan_ranks_eligible_and_never_mock_fills(client, test_db_session):
    from catalystiq.main import app
    from catalystiq.providers.market_data import MarketDataError, get_market_data_provider
    from catalystiq.schemas.market_data import FundamentalsSnapshot, Quote

    today = dt.date.today()
    # AAA rises fastest (best setup), BBB slower, BAD has no data (skipped).
    trends = {"AAA": 0.30, "BBB": 0.10, "SPY": 0.10}

    class _FakeProvider:
        def get_ohlcv(self, symbol, start, end=None, interval="1d"):
            symbol = symbol.upper()
            if symbol == "BAD":
                raise MarketDataError("no data for BAD")
            t = trends.get(symbol, 0.15)
            return _bars(_bizdays_ending(today, 300), _rising(300, 100, t, 4))

        def get_quote(self, symbol):
            return Quote(symbol=symbol.upper(), price=175.0, previous_close=174.0,
                         as_of=dt.datetime.now(dt.timezone.utc))

        def get_fundamentals(self, symbol):
            return FundamentalsSnapshot(symbol=symbol.upper(), sector="Technology",
                                        as_of=dt.datetime.now(dt.timezone.utc))

        def get_news(self, symbol, limit=10):
            return []

    app.dependency_overrides[get_market_data_provider] = lambda: _FakeProvider()
    try:
        r = client.get("/analysis/opportunity-scan", params={"top": 4, "symbols": "AAA,BBB,BAD"})
    finally:
        del app.dependency_overrides[get_market_data_provider]
    assert r.status_code == 200
    body = r.json()
    assert body["universe_size"] == 3
    # BAD is skipped (unfetchable), not mock-filled.
    syms = [c["symbol"] for c in body["candidates"]]
    assert "BAD" not in syms
    assert set(syms) <= {"AAA", "BBB"}
    # Ranked by score descending.
    scores = [c["score"] for c in body["candidates"]]
    assert scores == sorted(scores, reverse=True)
    assert body["ml"]["status"] == "not_available"
    # Every candidate is a real, available rule-based score.
    for c in body["candidates"]:
        assert c["status"] == "available" and c["score_type"] == "rule_based"


def test_opportunity_score_does_not_import_fred():
    src = (_REPO_ROOT / "catalystiq" / "analysis" / "opportunity_score.py").read_text()
    tree = ast.parse(src)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    assert not any(m.startswith("catalystiq.fred") for m in modules)
