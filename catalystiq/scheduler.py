"""Executes due scheduled orders (§1.1 Execution Zone / Rules Engine's
periodic re-run, per the scheduler/workers line in the build spec).

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
from catalystiq.providers.broker import BrokerError, BrokerProvider
from catalystiq.schemas.broker import NewOrder

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 15


def run_due_scheduled_orders(db: Session, broker: BrokerProvider) -> list[models.ScheduledOrder]:
    """Submits every pending, due ScheduledOrder through `broker`. Returns the
    rows it touched (now updated to submitted/failed) for callers/tests."""
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
        try:
            order = NewOrder(**row.order_json)
            result = broker.submit_order(order)
            row.status = "submitted"
            row.broker_order_id = str(result.get("id")) if isinstance(result, dict) else None
        except Exception as exc:  # broker rejection, bad stored payload, etc.
            row.status = "failed"
            row.error_detail = str(exc)

    if due:
        db.commit()
    return due


async def scheduler_loop(session_factory, get_broker) -> None:
    """Runs run_due_scheduled_orders on a fixed interval until cancelled.

    `session_factory`/`get_broker` are injected (not imported directly) so
    this loop is trivially testable without a running event loop needing a
    real broker/DB.
    """
    while True:
        try:
            db = session_factory()
            try:
                broker = get_broker()
                run_due_scheduled_orders(db, broker)
            finally:
                db.close()
        except BrokerError:
            # Broker isn't configured (yet) - retry next cycle rather than
            # crashing the loop or discarding the queued orders.
            pass
        except Exception:  # pragma: no cover - defensive, keeps the loop alive
            logger.exception("Scheduled-order poll failed")

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
