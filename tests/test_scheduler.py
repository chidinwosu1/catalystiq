"""Tests for the scheduled-order background executor (catalystiq/scheduler.py)."""
import datetime as dt
from unittest.mock import MagicMock

from catalystiq.db import models
from catalystiq.providers.broker import BrokerError
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


def test_submits_due_pending_orders(test_db_session):
    row = make_row(test_db_session, minutes_from_now=-1)
    broker = MagicMock()
    broker.submit_order.return_value = {"id": "order-123", "status": "accepted"}

    touched = run_due_scheduled_orders(test_db_session, broker)

    assert len(touched) == 1
    assert touched[0].status == "submitted"
    assert touched[0].broker_order_id == "order-123"
    broker.submit_order.assert_called_once()


def test_ignores_future_orders(test_db_session):
    make_row(test_db_session, minutes_from_now=30)
    broker = MagicMock()

    touched = run_due_scheduled_orders(test_db_session, broker)

    assert touched == []
    broker.submit_order.assert_not_called()


def test_ignores_non_pending_orders(test_db_session):
    make_row(test_db_session, status="cancelled", minutes_from_now=-5)
    make_row(test_db_session, status="submitted", minutes_from_now=-5)
    broker = MagicMock()

    touched = run_due_scheduled_orders(test_db_session, broker)

    assert touched == []
    broker.submit_order.assert_not_called()


def test_marks_failed_on_broker_rejection(test_db_session):
    make_row(test_db_session, minutes_from_now=-1)
    broker = MagicMock()
    broker.submit_order.side_effect = BrokerError("insufficient buying power")

    touched = run_due_scheduled_orders(test_db_session, broker)

    assert touched[0].status == "failed"
    assert "insufficient buying power" in touched[0].error_detail


def test_processes_multiple_due_orders_independently(test_db_session):
    make_row(test_db_session, minutes_from_now=-1, symbol="AAPL")
    make_row(test_db_session, minutes_from_now=-2, symbol="MSFT")
    broker = MagicMock()
    broker.submit_order.side_effect = [
        {"id": "1"},
        BrokerError("rejected"),
    ]

    touched = run_due_scheduled_orders(test_db_session, broker)

    statuses = {row.symbol: row.status for row in touched}
    assert statuses == {"AAPL": "submitted", "MSFT": "failed"}
