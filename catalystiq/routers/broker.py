"""Paper-trading endpoints (§1.1 Execution Zone), moved here unchanged from app.py."""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from catalystiq.auth import verify_action_key
from catalystiq.db import models
from catalystiq.db.base import get_db
from catalystiq.providers.broker import (
    BrokerError,
    BrokerProvider,
    OrderNotFoundError,
    get_broker_provider,
)
from catalystiq.schemas.broker import (
    AccountInfo,
    NewOrder,
    Position,
    ScheduledOrderCreate,
    ScheduledOrderRecord,
)

router = APIRouter(
    prefix="/paper",
    tags=["paper-trading"],
    dependencies=[Depends(verify_action_key)],
)


def _to_record(row: models.ScheduledOrder) -> ScheduledOrderRecord:
    return ScheduledOrderRecord(
        id=row.id,
        symbol=row.symbol,
        order=NewOrder(**row.order_json),
        scheduled_at=row.scheduled_at,
        status=row.status,
        broker_order_id=row.broker_order_id,
        error_detail=row.error_detail,
        created_at=row.created_at,
    )


@router.get("/account", response_model=AccountInfo)
def get_paper_account(broker: BrokerProvider = Depends(get_broker_provider)):
    try:
        return broker.get_account()
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/positions", response_model=list[Position])
def get_paper_positions(broker: BrokerProvider = Depends(get_broker_provider)):
    try:
        return broker.get_positions()
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/orders")
def get_paper_orders(broker: BrokerProvider = Depends(get_broker_provider)):
    try:
        return broker.get_orders()
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/orders")
def submit_paper_order(order: NewOrder, broker: BrokerProvider = Depends(get_broker_provider)):
    try:
        return broker.submit_order(order)
    except BrokerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/orders/{order_id}")
def get_paper_order(order_id: str, broker: BrokerProvider = Depends(get_broker_provider)):
    try:
        return broker.get_order(order_id)
    except OrderNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.delete("/orders/{order_id}")
def cancel_paper_order(order_id: str, broker: BrokerProvider = Depends(get_broker_provider)):
    try:
        broker.cancel_order(order_id)
        return {"status": "cancellation_requested", "order_id": order_id}
    except BrokerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/scheduled-orders", response_model=ScheduledOrderRecord)
def create_scheduled_order(payload: ScheduledOrderCreate, db: Session = Depends(get_db)):
    """Queues an order for future submission - executed by the background
    poller in catalystiq/scheduler.py, not immediately."""
    row = models.ScheduledOrder(
        symbol=payload.order.symbol.upper(),
        order_json=payload.order.model_dump(mode="json"),
        scheduled_at=payload.scheduled_at.astimezone(dt.timezone.utc).replace(tzinfo=None),
        status="pending",
        created_at=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_record(row)


@router.get("/scheduled-orders", response_model=list[ScheduledOrderRecord])
def list_scheduled_orders(db: Session = Depends(get_db)):
    rows = db.query(models.ScheduledOrder).order_by(models.ScheduledOrder.scheduled_at).all()
    return [_to_record(r) for r in rows]


@router.delete("/scheduled-orders/{scheduled_order_id}", response_model=ScheduledOrderRecord)
def cancel_scheduled_order(scheduled_order_id: int, db: Session = Depends(get_db)):
    row = db.get(models.ScheduledOrder, scheduled_order_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Scheduled order not found.")
    if row.status != "pending":
        raise HTTPException(
            status_code=409, detail=f"Cannot cancel a scheduled order that is {row.status}."
        )
    row.status = "cancelled"
    db.commit()
    db.refresh(row)
    return _to_record(row)
