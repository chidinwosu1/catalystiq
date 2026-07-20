"""The background universe warmer: it targets the scan universe + SPY + sector
ETFs (deduped), and warms each via ensure_fresh, counting outcomes without ever
raising on a per-symbol failure."""
from __future__ import annotations

import catalystiq.pipelines.market_price_pipeline as mpp
from catalystiq.analysis.opportunity_score import SCAN_UNIVERSE
from catalystiq.pipelines.universe_warmer import warm_symbols, warm_universe
from catalystiq.providers.market_data import MarketDataError


def test_warm_symbols_covers_universe_spy_and_sector_etfs():
    syms = warm_symbols()
    assert "SPY" in syms
    for s in SCAN_UNIVERSE:
        assert s in syms
    # Governed sector ETFs for the universe (e.g. Technology -> XLK).
    assert "XLK" in syms
    # De-duplicated.
    assert len(syms) == len(set(syms))


def test_warm_universe_calls_ensure_fresh_per_symbol_and_counts(monkeypatch):
    seen: list[str] = []

    def fake_ensure_fresh(symbol, provider, db, *args, **kwargs):
        seen.append(symbol)
        if symbol == "SPY":
            return None  # already fresh -> skipped (no provider call)
        if symbol == "XLK":
            raise MarketDataError("Too Many Requests. Rate limited")  # failed
        return object()  # warmed

    monkeypatch.setattr(mpp, "ensure_fresh", fake_ensure_fresh)

    result = warm_universe(provider=object(), db=object())

    assert set(seen) == set(warm_symbols())  # every warm symbol attempted
    assert result["skipped"] >= 1  # SPY
    assert result["failed"] >= 1  # XLK rate-limited
    assert result["warmed"] >= 1
    assert result["warmed"] + result["skipped"] + result["failed"] == len(warm_symbols())


def test_warm_universe_never_raises_on_failure(monkeypatch):
    def always_fail(symbol, provider, db, *args, **kwargs):
        raise MarketDataError("down")

    monkeypatch.setattr(mpp, "ensure_fresh", always_fail)
    result = warm_universe(provider=object(), db=object())
    assert result["failed"] == len(warm_symbols())
    assert result["warmed"] == 0
