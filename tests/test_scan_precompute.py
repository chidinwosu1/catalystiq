"""Scan CPU fixes: (A) scoring on capped bars is score-identical to full 5y
history, and (B) the background warmer precomputes the scan into the cache so
the user-facing request is a pure cache read (no scoring loop)."""
from __future__ import annotations

import datetime as dt
import random

import catalystiq.analysis.opportunity_score as osmod
from catalystiq.analysis.opportunity_score import (
    build_opportunity_score,
    clear_scan_cache,
    refresh_scan_cache,
    scan_universe_cached,
)
from catalystiq.schemas.market_data import OHLCVBar, Quote


def _bizdays(n):
    out = []
    d = dt.date.today()
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= dt.timedelta(days=1)
    return list(reversed(out))


def _series(n, seed):
    r = random.Random(seed)
    dates = _bizdays(n)
    px = 100.0
    out = []
    for day in dates:
        px *= 1 + r.uniform(-0.02, 0.022)
        out.append(OHLCVBar(date=day, open=px * 0.99, high=px * 1.02, low=px * 0.98,
                            close=px, volume=int(2e6 + r.random() * 1e6)))
    return out


# --- Fix A: capped-bar scoring is identical to full-history scoring ---------


def test_scoring_capped_bars_matches_full_history():
    now = dt.datetime.now(dt.timezone.utc)
    for seed in range(5):
        full = _series(1300, seed)
        mb = _series(1300, seed + 100)
        sb = _series(1300, seed + 200)
        ref = build_opportunity_score("X", full, now=now, market_bars=mb,
                                      sector_bars=sb, sector_symbol="XLK")
        capped = build_opportunity_score("X", full[-300:], now=now, market_bars=mb[-300:],
                                         sector_bars=sb[-300:], sector_symbol="XLK")
        # The score (and each factor) must be unchanged - the cap only removes
        # bars beyond any indicator's lookback.
        assert capped.score == ref.score
        assert capped.status == ref.status
        assert [f.score for f in capped.factors] == [f.score for f in ref.factors]


# --- Fix B: warmer precomputes the scan; the endpoint reads it from cache ----


class _FastProvider:
    def get_ohlcv(self, symbol, start, end=None, interval="1d"):
        return []  # empty history -> fast; loop-run counting is what matters

    def get_quote(self, symbol):
        return Quote(symbol=symbol.upper(), price=1.0, previous_close=1.0,
                     as_of=dt.datetime.now(dt.timezone.utc))

    def get_fundamentals(self, symbol):
        raise AssertionError("scan must not fetch fundamentals")

    def get_news(self, symbol, limit=10):
        return []


def test_warmer_precompute_makes_endpoint_a_cache_hit(test_db_session, monkeypatch):
    clear_scan_cache()
    calls = {"n": 0}
    original = osmod.scan_universe

    def spy(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(osmod, "scan_universe", spy)

    provider = _FastProvider()
    now = dt.datetime.now(dt.timezone.utc)

    # Warmer precomputes top=4 for the default universe.
    refresh_scan_cache(provider, test_db_session, now, tops=(4,))
    assert calls["n"] == 1

    # The endpoint call (default universe, top=4) is now a cache hit - the
    # expensive scoring loop does NOT run on the request path.
    scan = scan_universe_cached(provider, test_db_session, now, top=4, ttl_seconds=1800)
    assert calls["n"] == 1
    assert scan.top == 4
    clear_scan_cache()
