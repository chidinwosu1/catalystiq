"""Paper-trading endpoints (§1.1 Execution Zone).

Order submission is disabled by default and gated (§13): paper and live are
separate flags (live is refused until separately approved), and every
submission requires a single-use confirmation token bound to the exact order
details the user reviewed (symbol, side, qty/notional, type, prices, the
estimated max loss, and the account). See catalystiq/orders.py.
"""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from catalystiq.auth import verify_action_key
from catalystiq.config import Settings, get_settings
from catalystiq.db import models
from catalystiq.db.base import get_db
from catalystiq.orders import (
    OrderConfirmationError,
    estimate_max_loss,
    mint_token,
    verify_and_consume,
)
from catalystiq.providers.broker import (
    BrokerError,
    BrokerProvider,
    OrderNotFoundError,
    get_broker_provider,
)
from catalystiq.schemas.broker import (
    AccountInfo,
    ConfirmedOrder,
    NewOrder,
    OrderConfirmationResponse,
    OrderReview,
    Position,
    ScheduledOrderCreate,
    ScheduledOrderRecord,
)

router = APIRouter(
    prefix="/paper",
    tags=["paper-trading"],
    dependencies=[Depends(verify_action_key)],
)


def assert_submission_allowed(settings: Settings) -> str:
    """Enforce the submission gate (§13). Returns the active trading mode, or
    raises 403. Live is refused even if its flag is on - it stays unavailable
    until separately approved."""
    mode = settings.trading_mode
    if mode == "live":
        raise HTTPException(
            status_code=403,
            detail="Live order submission is not available (separate approval required).",
        )
    if mode != "paper":
        raise HTTPException(status_code=403, detail=f"Unknown trading mode {mode!r}.")
    if not settings.enable_paper_order_submission:
        raise HTTPException(
            status_code=403,
            detail="Paper order submission is disabled. Set ENABLE_PAPER_ORDER_SUBMISSION and "
            "confirm each order explicitly.",
        )
    if not settings.order_confirmation_secret:
        raise HTTPException(
            status_code=403,
            detail="Order submission requires ORDER_CONFIRMATION_SECRET to be configured.",
        )
    return mode


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


@router.get("/connection-test")
def broker_connection_test():
    """Read-only Webull reachability check (§13). Never places/cancels an
    order. Returns ok/failure without exposing credentials; a construction/
    config failure is reported as ok=False rather than a 502."""
    try:
        broker = get_broker_provider()
    except BrokerError as exc:
        return {"provider": "webull", "ok": False, "detail": str(exc)}
    if hasattr(broker, "connection_test"):
        return broker.connection_test()
    try:
        broker.get_orders()
        return {"provider": "webull", "ok": True, "detail": "reachable (read-only)"}
    except BrokerError as exc:
        return {"provider": "webull", "ok": False, "detail": str(exc)}


@router.post("/orders/confirm", response_model=OrderConfirmationResponse)
def confirm_paper_order(
    payload: ConfirmedOrder,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Step 1 of submission (§13): review the exact order details and receive
    a single-use, short-lived token bound to them. `payload.confirmation_token`
    is ignored here (this is where the token is issued)."""
    mode = assert_submission_allowed(settings)
    order = payload.order
    max_loss = estimate_max_loss(order)
    review = OrderReview(
        symbol=order.symbol.upper(), side=order.side, type=order.type,
        time_in_force=order.time_in_force, qty=order.qty, notional=order.notional,
        limit_price=order.limit_price, stop_price=order.stop_price,
        estimated_max_loss=max_loss, account_id=payload.account_id, mode=mode,
    )
    token, expires_at = mint_token(
        db, order, account_id=payload.account_id, mode=mode, estimated_max_loss=max_loss,
        secret=settings.order_confirmation_secret,
        ttl_seconds=settings.order_confirmation_ttl_seconds,
    )
    return OrderConfirmationResponse(review=review, confirmation_token=token, expires_at=expires_at)


@router.post("/orders")
def submit_paper_order(
    payload: ConfirmedOrder,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    broker: BrokerProvider = Depends(get_broker_provider),
):
    """Step 2 of submission (§13): submit only with a valid single-use token
    bound to exactly these order details + account + mode. The token is
    consumed here; any change to the order invalidates it."""
    mode = assert_submission_allowed(settings)
    order = payload.order
    max_loss = estimate_max_loss(order)
    try:
        verify_and_consume(
            db, payload.confirmation_token, order, account_id=payload.account_id, mode=mode,
            estimated_max_loss=max_loss, secret=settings.order_confirmation_secret,
        )
    except OrderConfirmationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
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
