"""The user-facing opportunity scan never blocks on a cold scan: it serves the
cache (even slightly stale) and kicks a single-flight background compute,
returning a fast "warming up" placeholder only when nothing is cached yet.

See catalystiq/analysis/opportunity_score.scan_universe_fast.
"""
from __future__ import annotations

import datetime as dt

import pytest

import catalystiq.analysis.opportunity_score as ops
from catalystiq.analysis.opportunity_score import (
    _MAX_SCAN_TOP,
    _SCAN_CACHE,
    _SCAN_CACHE_LOCK,
    _SCAN_INFLIGHT,
    SCAN_UNIVERSE,
    _ScanCacheEntry,
    clear_scan_cache,
    scan_universe_fast,
)
from catalystiq.schemas.opportunity import OpportunityScan

NOW = dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc)


def _fake_scan(note: str, status: str = "ok") -> OpportunityScan:
    return OpportunityScan(
        as_of=NOW,
        formula_version="test",
        universe_size=0,
        eligible_count=0,
        top=4,
        candidates=[],
        ml=ops._ML_NOT_AVAILABLE,
        note=note,
        status=status,
    )


def _key(top: int = 4) -> tuple:
    return (SCAN_UNIVERSE, max(0, min(top, _MAX_SCAN_TOP)))


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Clean cache/in-flight state and record background-compute starts without
    spawning real threads or touching the DB/provider."""
    clear_scan_cache()
    _SCAN_INFLIGHT.clear()
    starts: list[tuple] = []
    monkeypatch.setattr(
        ops, "_start_background_scan", lambda top, universe, key: starts.append(key)
    )
    yield starts
    clear_scan_cache()
    _SCAN_INFLIGHT.clear()


def test_cold_cache_returns_warming_and_starts_one_background(_isolated):
    starts = _isolated
    scan = scan_universe_fast(NOW, top=4, ttl_seconds=1800)
    assert scan.candidates == []
    assert scan.note and "warming" in scan.note.lower()
    assert starts == [_key()]  # exactly one single-flight background compute


def test_warm_cache_returns_cached_without_background(_isolated):
    starts = _isolated
    with _SCAN_CACHE_LOCK:
        _SCAN_CACHE[_key()] = _ScanCacheEntry(scan=_fake_scan("REAL"), stored_at=100.0)
    # 200 - 100 = 100s elapsed, < 1800 ttl -> fresh.
    scan = scan_universe_fast(NOW, top=4, ttl_seconds=1800, monotonic=lambda: 200.0)
    assert scan.note == "REAL"
    assert starts == []  # no recompute for a fresh cache


def test_stale_cache_served_and_refreshed_in_background(_isolated):
    starts = _isolated
    with _SCAN_CACHE_LOCK:
        _SCAN_CACHE[_key()] = _ScanCacheEntry(scan=_fake_scan("STALE"), stored_at=0.0)
    # 5000s elapsed, > 1800 ttl -> stale: serve it, but refresh in background.
    scan = scan_universe_fast(NOW, top=4, ttl_seconds=1800, monotonic=lambda: 5000.0)
    assert scan.note == "STALE"  # served the real (stale) scan, not a placeholder
    assert starts == [_key()]  # and kicked a background refresh


def test_single_flight_does_not_double_start(_isolated):
    starts = _isolated
    _SCAN_INFLIGHT.add(_key())  # a compute is already running
    scan = scan_universe_fast(NOW, top=4, ttl_seconds=1800)
    assert scan.note and "warming" in scan.note.lower()
    assert starts == []  # not started again while one is in flight


def test_warming_placeholder_status(_isolated):
    # The cold-cache placeholder is machine-tagged "warming" so the client can
    # keep polling without parsing the note text.
    scan = scan_universe_fast(NOW, top=4, ttl_seconds=1800)
    assert scan.status == "warming"


def test_unavailable_cache_is_rewarmed_even_when_fresh(_isolated):
    # A cached "unavailable" scan is a transient data outage, not a durable
    # answer: the next request must re-kick a (single-flight) background scan
    # even while the entry is nominally fresh, so the page recovers as soon as
    # the upstream throttle lifts - instead of waiting out the cache TTL.
    starts = _isolated
    with _SCAN_CACHE_LOCK:
        _SCAN_CACHE[_key()] = _ScanCacheEntry(
            scan=_fake_scan("Market data is temporarily unavailable", status="unavailable"),
            stored_at=100.0,
        )
    scan = scan_universe_fast(NOW, top=4, ttl_seconds=1800, monotonic=lambda: 200.0)
    assert scan.status == "unavailable"  # serves the (stale) outage result now
    assert starts == [_key()]  # and re-warms in the background


def test_fresh_ok_cache_is_not_rewarmed(_isolated):
    # An "ok" scan (candidates, or a genuine "nothing qualifies") is a real
    # answer and must NOT trigger a needless re-warm while fresh.
    starts = _isolated
    with _SCAN_CACHE_LOCK:
        _SCAN_CACHE[_key()] = _ScanCacheEntry(scan=_fake_scan("REAL", status="ok"), stored_at=100.0)
    scan = scan_universe_fast(NOW, top=4, ttl_seconds=1800, monotonic=lambda: 200.0)
    assert scan.note == "REAL"
    assert starts == []
