"""Paper-trading endpoints (§1.1 Execution Zone), moved here unchanged from app.py."""
from fastapi import APIRouter, Depends, HTTPException

from catalystiq.auth import verify_action_key
from catalystiq.providers.broker import (
    BrokerError,
    BrokerProvider,
    OrderNotFoundError,
    get_broker_provider,
)
from catalystiq.schemas.broker import AccountInfo, NewOrder, Position

router = APIRouter(
    prefix="/paper",
    tags=["paper-trading"],
    dependencies=[Depends(verify_action_key)],
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
