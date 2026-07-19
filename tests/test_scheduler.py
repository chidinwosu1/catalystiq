"""Tests for the scheduled-order poller (catalystiq/scheduler.py).

Per §13 the poller NEVER submits an order automatically: it only flips a due
scheduled order to `due` so the UI can surface it for manual review/
confirmation. Submission always goes through the explicit confirm+token flow.
"""
import datetime as dt

from catalystiq.db import models
from catalystiq.scheduler import run_due_scheduled_orders


def make_row(db, *, status="pending", minutes_from_now=-5, symbol="AAPL"):
    row = models.ScheduledOrder(
        symbol=symbol,
        order_json={"symbol": symbol, "side": "buy", "type": "market", "qty": 1},
        scheduled_at=dt.datetime.utcnow() + dt.timedelta(minutes=minutes_from_now),
        status=status,
        created_at=dt.datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_due_orders_are_marked_due_not_submitted(test_db_session):
    row = make_row(test_db_session, minutes_from_now=-1)
    touched = run_due_scheduled_orders(test_db_session)
    assert len(touched) == 1
    # Marked ready for manual review - NOT submitted.
    assert touched[0].status == "due"
    assert touched[0].broker_order_id is None


def test_ignores_future_orders(test_db_session):
    make_row(test_db_session, minutes_from_now=30)
    assert run_due_scheduled_orders(test_db_session) == []


def test_ignores_non_pending_orders(test_db_session):
    make_row(test_db_session, status="cancelled", minutes_from_now=-5)
    make_row(test_db_session, status="submitted", minutes_from_now=-5)
    make_row(test_db_session, status="due", minutes_from_now=-5)
    assert run_due_scheduled_orders(test_db_session) == []


def test_processes_multiple_due_orders(test_db_session):
    make_row(test_db_session, minutes_from_now=-1, symbol="AAPL")
    make_row(test_db_session, minutes_from_now=-2, symbol="MSFT")
    touched = run_due_scheduled_orders(test_db_session)
    assert {row.symbol: row.status for row in touched} == {"AAPL": "due", "MSFT": "due"}


def test_poller_never_calls_a_broker(test_db_session):
    # The function no longer even accepts a broker - submission is not part of
    # the scheduled path at all.
    import inspect

    params = set(inspect.signature(run_due_scheduled_orders).parameters)
    assert params == {"db"}
