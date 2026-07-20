"""Background warmer that keeps Silver fresh for the opportunity-scan universe.

A cold opportunity scan is slow because `ensure_fresh` has to ingest history
for every universe symbol (+ SPY + sector ETFs) on the synchronous request path.
This loop does that ingest in the background on a calendar-aware interval, so the
user-facing scan finds Silver already fresh and returns from the DB in
sub-second time. It reuses `ensure_fresh`, so a symbol that is already fresh is a
no-op (no provider call), and it goes through the same governed market-data gate,
so it can't hammer a throttled endpoint. A per-symbol failure is logged and
skipped - it never takes the loop (or the app) down.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


def warm_symbols() -> list[str]:
    """The de-duplicated symbol set to keep warm: the scan universe, its market
    benchmark (SPY), and the governed sector ETFs it maps to."""
    from catalystiq.analysis.opportunity_score import SCAN_UNIVERSE
    from catalystiq.analysis.sectors import governed_sector_etf

    ordered = ["SPY", *SCAN_UNIVERSE]
    for sym in SCAN_UNIVERSE:
        etf = governed_sector_etf(sym)
        if etf:
            ordered.append(etf)

    seen: set[str] = set()
    out: list[str] = []
    for sym in ordered:
        u = sym.upper()
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def warm_universe(provider, db) -> dict[str, int]:
    """Bring Silver up to date for every warm symbol. Returns counts. Best
    effort: a symbol that can't be fetched (or is rate-limit-cooled-down) is
    counted as failed and skipped, never raised."""
    from catalystiq.pipelines.market_price_pipeline import ensure_fresh
    from catalystiq.providers.market_data import MarketDataError

    warmed = skipped = failed = 0
    for sym in warm_symbols():
        try:
            run = ensure_fresh(sym, provider, db)
            if run is None:
                skipped += 1  # already fresh - no provider call made
            else:
                warmed += 1
        except MarketDataError:
            failed += 1
        except Exception:  # pragma: no cover - defensive; keep warming others
            logger.exception("universe warm failed for %s", sym)
            failed += 1
    return {"warmed": warmed, "skipped": skipped, "failed": failed}


async def universe_warm_loop(session_factory, provider_factory, interval_seconds: float) -> None:
    """Run `warm_universe` immediately, then every `interval_seconds`, until
    cancelled. The blocking ingest runs in a worker thread so it never stalls
    the event loop. `session_factory`/`provider_factory` are injected for
    testability."""
    while True:
        try:
            db = session_factory()
            try:
                provider = provider_factory()
                result = await asyncio.to_thread(warm_universe, provider, db)
                logger.info("universe warm: %s", result)
            finally:
                db.close()
        except Exception:  # pragma: no cover - defensive, keeps the loop alive
            logger.exception("universe warm loop iteration failed")

        await asyncio.sleep(interval_seconds)
