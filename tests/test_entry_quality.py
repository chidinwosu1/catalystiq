"""Dynamic Entry Quality Score: deterministic intraday component math, strict
insufficient-data handling, the 0-100 weighting, ratings, and the versioned
contract. All offline (synthetic intraday bars, injected clock)."""
from __future__ import annotations

import datetime as dt

from catalystiq.analysis.entry_quality import (
    COMPONENT_WEIGHTS,
    FORMULA_VERSION,
    build_entry_quality_score,
    score_entry_quality,
)
from catalystiq.schemas.entry_quality import EntryQualityScore
from catalystiq.schemas.market_data import IntradayBar

# A regular-session open at 13:30 UTC (9:30 ET). Enough bars for RSI(14)/EMA(20).
_SESSION_OPEN = dt.datetime(2026, 7, 20, 13, 30, tzinfo=dt.timezone.utc)
_NOW = dt.datetime(2026, 7, 20, 16, 0, tzinfo=dt.timezone.utc)


def _bar(ts: dt.datetime, o, h, l, c, v) -> IntradayBar:
    return IntradayBar(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


def _session(
    open_ts: dt.datetime,
    closes: list[float],
    *,
    volume: float = 100_000,
    span_pct: float = 0.001,
) -> list[IntradayBar]:
    """Build 5-minute bars from a close path; each bar's high/low straddles its
    close by ``span_pct`` so the opening range and ATR are well-defined."""
    bars = []
    prev = closes[0]
    for i, c in enumerate(closes):
        ts = open_ts + dt.timedelta(minutes=5 * i)
        hi = max(prev, c) * (1 + span_pct)
        lo = min(prev, c) * (1 - span_pct)
        bars.append(_bar(ts, prev, hi, lo, c, volume))
        prev = c
    return bars


def _prior_sessions(n_sessions: int, bars_per: int, volume: float) -> list[IntradayBar]:
    """Flat prior sessions (constant price) purely to provide a relative-volume
    time-of-day baseline. Dated before the current session."""
    out: list[IntradayBar] = []
    for s in range(1, n_sessions + 1):
        day_open = _SESSION_OPEN - dt.timedelta(days=s)
        for i in range(bars_per):
            ts = day_open + dt.timedelta(minutes=5 * i)
            out.append(_bar(ts, 100.0, 100.1, 99.9, 100.0, volume))
    return out


# --- Static contract --------------------------------------------------------


def test_component_weights_total_100():
    assert sum(COMPONENT_WEIGHTS.values()) == 100
    assert set(COMPONENT_WEIGHTS) == {
        "vwap_distance", "ema9_distance", "intraday_rsi", "time_since_pullback",
        "relative_volume", "morning_range_extension", "risk_reward",
    }


def test_no_bars_is_insufficient_not_zero():
    r = build_entry_quality_score("TEST", [], now=_NOW)
    assert isinstance(r, EntryQualityScore)
    assert r.status == "insufficient_data"
    assert r.score is None and r.rating is None
    assert r.reason and "No intraday" in r.reason


def test_too_few_bars_is_insufficient():
    bars = _session(_SESSION_OPEN, [100.0, 100.1, 100.2])  # < _MIN_TODAY_BARS
    r = build_entry_quality_score("TEST", bars, now=_NOW)
    assert r.status == "insufficient_data"
    assert r.score is None


# --- A full, available score ------------------------------------------------


def _healthy_dataset():
    # A controlled uptrend that pulls back into VWAP near the end: rise, dip, hold.
    up = [100 + i * 0.05 for i in range(20)]          # steady morning rise
    pull = [up[-1] - i * 0.06 for i in range(1, 6)]   # a real pullback (>0.5%)
    hold = [pull[-1] + i * 0.01 for i in range(1, 6)] # consolidation near VWAP
    closes = up + pull + hold
    today = _session(_SESSION_OPEN, closes, volume=140_000)
    priors = _prior_sessions(20, len(closes), volume=100_000)
    return priors + today, closes


def test_full_score_is_available_and_in_range():
    bars, _ = _healthy_dataset()
    r = build_entry_quality_score("test", bars, now=_NOW, interval="5m")
    assert r.status == "available"
    assert r.symbol == "TEST"
    assert r.score_type == "entry_quality"
    assert r.formula_version == FORMULA_VERSION
    assert r.interval == "5m"
    assert r.component_coverage == "7/7"
    assert 0 <= r.score <= 100
    # Score is the exact integer sum of component sub-points.
    assert r.score == sum(c.score for c in r.components)
    assert r.rating in {
        "Excellent Entry", "Good Entry", "Acceptable", "Caution", "Poor Entry"
    }
    for c in r.components:
        assert c.formula_version == FORMULA_VERSION
        assert 0 <= c.score <= c.max_score


def test_data_as_of_is_last_bar_timestamp():
    bars, closes = _healthy_dataset()
    r = build_entry_quality_score("TEST", bars, now=_NOW)
    expected = _SESSION_OPEN + dt.timedelta(minutes=5 * (len(closes) - 1))
    assert r.data_as_of == expected


# --- Component behaviour: VWAP distance -------------------------------------


def test_extended_above_vwap_scores_low_on_vwap_component():
    # Parabolic run: price ends far above the session VWAP.
    closes = [100 + i * 0.5 for i in range(30)]
    bars = _prior_sessions(20, 30, 100_000) + _session(_SESSION_OPEN, closes, volume=120_000)
    r = build_entry_quality_score("TEST", bars, now=_NOW)
    vwap = next(c for c in r.components if c.name == "vwap_distance")
    assert vwap.inputs["distance_pct"] > 2.0
    assert vwap.score == 4  # >2% above VWAP -> lowest band


def test_holding_vwap_scores_high_on_vwap_component():
    # Rise then settle right back onto VWAP.
    closes = [100 + i * 0.03 for i in range(20)] + [100.30 - i * 0.02 for i in range(1, 11)]
    bars = _prior_sessions(20, 30, 100_000) + _session(_SESSION_OPEN, closes, volume=110_000)
    r = build_entry_quality_score("TEST", bars, now=_NOW)
    vwap = next(c for c in r.components if c.name == "vwap_distance")
    assert abs(vwap.inputs["distance_pct"]) <= 1.0
    assert vwap.score >= 16


# --- Component behaviour: intraday RSI --------------------------------------


def test_overbought_rsi_is_penalized():
    closes = [100 + i * 0.6 for i in range(30)]  # relentless up -> RSI > 75
    bars = _prior_sessions(20, 30, 100_000) + _session(_SESSION_OPEN, closes, volume=120_000)
    r = build_entry_quality_score("TEST", bars, now=_NOW)
    rsi = next(c for c in r.components if c.name == "intraday_rsi")
    assert rsi.inputs["intraday_rsi"] > 75
    assert rsi.score == 3


# --- Component behaviour: relative volume -----------------------------------


def test_relative_volume_insufficient_without_priors():
    closes = [100 + i * 0.05 for i in range(25)]
    bars = _session(_SESSION_OPEN, closes, volume=100_000)  # no prior sessions
    r = build_entry_quality_score("TEST", bars, now=_NOW)
    assert r.status == "insufficient_data"
    relvol = next(c for c in r.components if c.name == "relative_volume")
    assert relvol.status == "insufficient_data"
    assert relvol.score is None


def test_healthy_relative_volume_scores_top_band():
    # Today's cumulative volume ~1.4x the prior-session same-time baseline.
    closes = [100 + i * 0.03 for i in range(25)]
    bars = _prior_sessions(20, 25, 100_000) + _session(_SESSION_OPEN, closes, volume=140_000)
    r = build_entry_quality_score("TEST", bars, now=_NOW)
    relvol = next(c for c in r.components if c.name == "relative_volume")
    assert 1.2 <= relvol.inputs["relative_volume"] <= 2.5
    assert relvol.score == 15


# --- Ratings map ------------------------------------------------------------


def test_rating_bands():
    from catalystiq.analysis.entry_quality import _rating

    assert _rating(95) == "Excellent Entry"
    assert _rating(85) == "Good Entry"
    assert _rating(72) == "Acceptable"
    assert _rating(63) == "Caution"
    assert _rating(40) == "Poor Entry"


# --- Orchestrator degradation (no fabricated numbers) -----------------------


class _NoIntradayProvider:
    """A provider WITHOUT get_intraday_ohlcv -> entry quality must degrade to
    insufficient_data, never a fabricated score."""


class _EmptyIntradayProvider:
    def get_intraday_ohlcv(self, symbol, *, interval="5m", days=20):
        return []


class _RaisingIntradayProvider:
    def get_intraday_ohlcv(self, symbol, *, interval="5m", days=20):
        raise RuntimeError("provider boom")


def test_orchestrator_degrades_when_provider_lacks_intraday():
    r = score_entry_quality("AAPL", _NoIntradayProvider(), _NOW)
    assert r.status == "insufficient_data"
    assert r.score is None
    assert "not available" in r.reason


def test_orchestrator_degrades_on_empty_and_on_error():
    empty = score_entry_quality("AAPL", _EmptyIntradayProvider(), _NOW)
    assert empty.status == "insufficient_data" and empty.score is None
    raised = score_entry_quality("AAPL", _RaisingIntradayProvider(), _NOW)
    assert raised.status == "insufficient_data" and raised.score is None


def test_orchestrator_scores_from_a_real_intraday_provider():
    bars, _ = _healthy_dataset()

    class _GoodProvider:
        def get_intraday_ohlcv(self, symbol, *, interval="5m", days=20):
            return bars

    r = score_entry_quality("TEST", _GoodProvider(), _NOW)
    assert r.status == "available"
    assert 0 <= r.score <= 100


# --- Plain-language Entry Check verdict layer -------------------------------

_USER_STATUSES = {
    "Entry Looks Favorable", "Almost Ready — Keep Watching", "Wait for a Lower Price",
    "Avoid This Entry for Now", "Cannot Evaluate Right Now",
}


def test_entry_check_always_present_even_when_insufficient():
    r = build_entry_quality_score("TEST", [], now=_NOW)
    assert r.status == "insufficient_data"
    ec = r.entry_check
    assert ec is not None
    assert ec.system_status == "data_unavailable"
    assert ec.user_status == "Cannot Evaluate Right Now"
    assert ec.data_state == "unavailable"
    # Never zero-filled: missing values are None, not 0.
    assert ec.current_price is None and ec.preferred_entry_low is None
    assert ec.exit_level is None and ec.reward_to_risk is None


def test_entry_check_available_has_plain_language_and_prices():
    bars, _ = _healthy_dataset()
    r = build_entry_quality_score("TEST", bars, now=_NOW, setup_is_strong=True)
    ec = r.entry_check
    assert ec is not None
    assert ec.system_status in {"favorable", "almost_ready", "wait_for_pullback", "avoid"}
    assert ec.user_status in _USER_STATUSES
    assert ec.data_state == "current"
    # A user-facing verdict never says "Buy Now".
    assert "buy now" not in ec.user_status.lower()
    assert "buy now" not in ec.headline.lower()
    # Prices are real numbers with a well-ordered preferred band.
    assert ec.preferred_entry_low is not None and ec.preferred_entry_high is not None
    assert ec.preferred_entry_low <= ec.preferred_entry_high
    assert ec.exit_level is not None and ec.exit_level < ec.preferred_entry_low
    # Exactly four plain-language checklist reasons.
    assert len(ec.reasons) == 4
    assert {r.state for r in ec.reasons} <= {"good", "bad", "pending"}
    assert all(r.label and not any(t in r.label for t in ("VWAP", "EMA", "RSI", "ATR")) for r in ec.reasons)
    # The simple explanation avoids technical jargon.
    for term in ("VWAP", "EMA", "RSI", "ATR", "reward-to-risk", "invalidation"):
        assert term.lower() not in ec.headline.lower()
        assert term.lower() not in ec.what_to_do.lower()


def test_extended_price_waits_for_a_lower_price():
    # Parabolic run leaves price far above the VWAP/EMA entry zone.
    closes = [100 + i * 0.5 for i in range(30)]
    bars = _prior_sessions(20, 30, 100_000) + _session(_SESSION_OPEN, closes, volume=120_000)
    r = build_entry_quality_score("TEST", bars, now=_NOW, setup_is_strong=True)
    ec = r.entry_check
    assert ec.system_status == "wait_for_pullback"
    assert ec.user_status == "Wait for a Lower Price"
    assert ec.distance_to_entry_pct > 0
    # A strong setup is reflected in the checklist and headline.
    setup = next(x for x in ec.reasons if x.key == "setup_strong")
    assert setup.state == "good"
    assert "strong overall setup" in ec.headline


def test_latest_price_moves_verdict_without_touching_component_scores():
    # Same completed candles; a fresher (lower) live price pulls the verdict
    # toward the entry zone while the seven component scores are unchanged.
    bars, _ = _healthy_dataset()
    base = build_entry_quality_score("TEST", bars, now=_NOW, setup_is_strong=True)
    vwap = float(next(c for c in base.components if c.name == "vwap_distance").inputs["vwap"])
    withprice = build_entry_quality_score(
        "TEST", bars, now=_NOW, setup_is_strong=True, latest_price=vwap
    )
    # Component scores (completed-candle) are identical regardless of latest_price.
    assert [c.score for c in base.components] == [c.score for c in withprice.components]
    assert base.score == withprice.score
    # But the verdict's current price tracked the live quote.
    assert withprice.entry_check.current_price == round(vwap, 2)


def test_orchestrator_uses_best_effort_quote_for_current_price():
    from catalystiq.schemas.market_data import Quote

    bars, _ = _healthy_dataset()

    class _QuoteProvider:
        def get_intraday_ohlcv(self, symbol, *, interval="5m", days=20):
            return bars

        def get_quote(self, symbol):
            return Quote(symbol=symbol.upper(), price=123.45,
                         as_of=dt.datetime.now(dt.timezone.utc))

    r = score_entry_quality("TEST", _QuoteProvider(), _NOW, setup_is_strong=True)
    assert r.entry_check.current_price == 123.45


# --- Integration: both scores ride together on each scan candidate ----------


def _bizdays_ending(end: dt.date, n: int) -> list[dt.date]:
    days: list[dt.date] = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= dt.timedelta(days=1)
    return list(reversed(days))


def _daily_bars(dates, closes, volume=2_000_000):
    from catalystiq.schemas.market_data import OHLCVBar

    return [OHLCVBar(date=d, open=c, high=c * 1.01, low=c * 0.99, close=c, volume=volume)
            for d, c in zip(dates, closes)]


def _rising(n, base, trend, amp):
    import math

    return [base + trend * i + amp * math.sin(i / 5.0) for i in range(n)]


def test_scan_does_not_block_on_entry_quality(client, test_db_session, monkeypatch):
    """The scan (background warm path) must NOT fetch intraday data - candidates
    render on Setup Strength alone, and the card polls Entry Quality separately.
    So scan candidates carry entry_quality=None and the intraday provider is
    never touched during the scan."""
    import catalystiq.providers.market_data as market_data
    from catalystiq.analysis.entry_quality import clear_entry_quality_cache
    from catalystiq.analysis.opportunity_score import clear_scan_cache
    from catalystiq.main import app
    from catalystiq.providers.market_data import get_market_data_provider
    from catalystiq.schemas.market_data import FundamentalsSnapshot, Quote

    today = dt.date.today()
    intraday_calls = {"n": 0}

    class _FakeProvider:
        PROVIDER_NAME = "fake_scan"

        def get_ohlcv(self, symbol, start, end=None, interval="1d"):
            return _daily_bars(_bizdays_ending(today, 300), _rising(300, 100, 0.25, 4))

        def get_intraday_ohlcv(self, symbol, *, interval="5m", days=20):
            intraday_calls["n"] += 1  # must NOT be hit during the scan
            return []

        def get_quote(self, symbol):
            return Quote(symbol=symbol.upper(), price=175.0, previous_close=174.0,
                         as_of=dt.datetime.now(dt.timezone.utc))

        def get_fundamentals(self, symbol):
            return FundamentalsSnapshot(symbol=symbol.upper(), sector="Technology",
                                        as_of=dt.datetime.now(dt.timezone.utc))

        def get_news(self, symbol, limit=10):
            return []

    provider = _FakeProvider()
    clear_scan_cache()
    clear_entry_quality_cache()
    app.dependency_overrides[get_market_data_provider] = lambda: provider
    monkeypatch.setattr(market_data, "get_intraday_market_data_provider", lambda: provider)
    try:
        r = client.get("/analysis/opportunity-scan", params={"top": 2, "symbols": "NVDA,AAPL"})
    finally:
        del app.dependency_overrides[get_market_data_provider]
        clear_scan_cache()
        clear_entry_quality_cache()

    assert r.status_code == 200
    candidates = r.json()["candidates"]
    assert candidates
    # Candidates are real Setup Strength scores; entry_quality is NOT attached
    # by the scan (the card polls it), and the scan never fetched intraday data.
    for c in candidates:
        assert c["status"] == "available"
        assert c["formula_version"] == "opportunity_score_v1"
        assert c["entry_quality"] is None
    assert intraday_calls["n"] == 0


def test_entry_quality_endpoint_serves_the_card_feed(client, test_db_session, monkeypatch):
    """The per-symbol endpoint the cards / pop-out poll returns a full Entry
    Quality + Entry Check, sourced from the dedicated intraday provider."""
    import catalystiq.providers.market_data as market_data
    from catalystiq.analysis.entry_quality import clear_entry_quality_cache

    intraday, _ = _healthy_dataset()

    class _IntradayProvider:
        PROVIDER_NAME = "fake_card_feed"

        def get_intraday_ohlcv(self, symbol, *, interval="5m", days=20):
            return intraday

        def get_quote(self, symbol):
            from catalystiq.schemas.market_data import Quote

            return Quote(symbol=symbol.upper(), price=175.0,
                         as_of=dt.datetime.now(dt.timezone.utc))

    clear_entry_quality_cache()
    monkeypatch.setattr(
        market_data, "get_intraday_market_data_provider", lambda: _IntradayProvider()
    )
    try:
        r = client.get("/analysis/NVDA/entry-quality")
    finally:
        clear_entry_quality_cache()

    assert r.status_code == 200
    body = r.json()
    assert body["score_type"] == "entry_quality"
    assert body["status"] == "available"
    assert 0 <= body["score"] <= 100
    assert body["entry_check"] is not None
    assert body["entry_check"]["system_status"] in {
        "favorable", "almost_ready", "wait_for_pullback", "avoid"
    }
