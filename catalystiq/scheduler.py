"""Marks due scheduled orders ready for manual review - it does NOT submit
them (§13).

Order submission never happens automatically. When a scheduled order's time
passes, this poller flips it from `pending` to `due` so the UI can surface it
(a notification / a prepared Trade Ticket draft) for a human to review and
explicitly confirm via the confirm+submit flow. Actual submission always
requires the paper-submission flag to be on AND a single-use confirmation
token bound to the exact order details (see catalystiq/routers/broker.py and
catalystiq/orders.py).

This is an in-process poller, not a real task queue (no Celery/Redis) - it
only runs while this FastAPI process is alive. See catalystiq/main.py's
lifespan for where it's started, and the README for that limitation.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy.orm import Session

from catalystiq.db import models

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 15


def run_due_scheduled_orders(db: Session) -> list[models.ScheduledOrder]:
    """Flip every pending, due ScheduledOrder to `due` (ready for manual
    review). NEVER submits an order. Returns the rows it touched so a caller
    can surface a notification/draft for each."""
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    due = (
        db.query(models.ScheduledOrder)
        .filter(
            models.ScheduledOrder.status == "pending",
            models.ScheduledOrder.scheduled_at <= now,
        )
        .all()
    )

    for row in due:
        row.status = "due"

    if due:
        db.commit()
    return due


async def scheduler_loop(session_factory) -> None:
    """Runs run_due_scheduled_orders on a fixed interval until cancelled.
    `session_factory` is injected so the loop is trivially testable."""
    while True:
        try:
            db = session_factory()
            try:
                run_due_scheduled_orders(db)
            finally:
                db.close()
        except Exception:  # pragma: no cover - defensive, keeps the loop alive
            logger.exception("Scheduled-order poll failed")

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
