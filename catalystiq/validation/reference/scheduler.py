"""Runs the reference-calculation adapter asynchronously - this, CI
(.github/workflows/reference_validation.yml), and manual dispatch before
promoting a calculation_version are the only places it ever runs. Never in
the synchronous user-request path.

Same in-process-poller pattern as catalystiq/scheduler.py's
scheduler_loop (no Celery/Redis task queue) - only runs while this FastAPI
process is alive, same documented limitation.

Each cycle:
  1. Processes every GoldCalculationRun the synchronous anomaly check
     flagged (catalystiq/validation/reference/anomaly.py, wired in via
     market_price_pipeline.py's _persist_gold()) - unconditionally, not
     subject to sampling.
  2. Samples a configurable percentage (settings.reference_validation_sample_rate)
     of other recently-succeeded, not-yet-reference-checked runs.
"""
from __future__ import annotations

import asyncio
import logging
import random

from sqlalchemy.orm import Session

from catalystiq.db import models
from catalystiq.validation.reference.comparator import run_reference_check

logger = logging.getLogger(__name__)


def run_reference_validation_cycle(db: Session, sample_rate: float) -> list[int]:
    """Processes every flagged run, then samples `sample_rate` of other
    recently-succeeded, not-yet-checked runs. Returns the
    GoldCalculationRun ids actually checked this cycle (for callers/
    tests)."""
    processed_ids: list[int] = []

    flagged = (
        db.query(models.GoldCalculationRun)
        .filter(
            models.GoldCalculationRun.flagged_for_reference_check.is_(True),
            models.GoldCalculationRun.status == "succeeded",
        )
        .all()
    )
    for run in flagged:
        run_reference_check(run.id, db)
        processed_ids.append(run.id)

    candidates = (
        db.query(models.GoldCalculationRun)
        .filter(
            models.GoldCalculationRun.status == "succeeded",
            models.GoldCalculationRun.reference_checked_at.is_(None),
            models.GoldCalculationRun.flagged_for_reference_check.is_(False),
        )
        .all()
    )
    sample_size = round(len(candidates) * sample_rate)
    sampled = random.sample(candidates, sample_size) if sample_size > 0 else []
    for run in sampled:
        run_reference_check(run.id, db)
        processed_ids.append(run.id)

    return processed_ids


async def reference_validation_loop(session_factory, sample_rate: float, interval_seconds: int) -> None:
    """Runs run_reference_validation_cycle on a fixed interval until
    cancelled. `session_factory` is injected (not imported directly) so
    this loop is trivially testable without a running event loop."""
    while True:
        try:
            db = session_factory()
            try:
                run_reference_validation_cycle(db, sample_rate)
            finally:
                db.close()
        except Exception:  # pragma: no cover - defensive, keeps the loop alive
            logger.exception("Reference validation cycle failed")

        await asyncio.sleep(interval_seconds)
