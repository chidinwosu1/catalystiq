"""Server-side, single-use order-confirmation tokens (§13).

Any order-submission path must present a token that is:
  - cryptographically bound to the EXACT order details the user reviewed -
    symbol, side, quantity/notional, order type, limit/stop prices, the
    estimated maximum loss, the account, and the trading mode (paper/live),
  - short-lived (expires), and
  - single-use (consumed on submission; a replay is rejected).

Tampering with any bound detail invalidates the token because the details
are folded into an HMAC. Single-use + expiry are enforced via a server-side
OrderConfirmationToken row, so a token can't be replayed or reused.

This does not by itself enable submission - that is separately gated by the
paper/live flags (off by default) in the broker router.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import secrets

from sqlalchemy.orm import Session

from catalystiq.db import models
from catalystiq.schemas.broker import NewOrder

DEFAULT_TTL_SECONDS = 300


class OrderConfirmationError(RuntimeError):
    """Raised when a confirmation token is missing, malformed, expired,
    already used, or doesn't match the order it's presented with."""


def estimate_max_loss(order: NewOrder) -> float | None:
    """Best-effort estimated maximum loss for review/binding (§13).

    - With a protective stop and a quantity + entry reference: qty * the
      adverse move to the stop.
    - Otherwise the worst case is the full position value (a long can go to
      zero): notional, or qty * entry reference.
    - Unknown (a bare market order with no price reference): None - the
      caller must supply the reviewed figure so something concrete is bound.
    """
    entry_ref = order.limit_price or order.stop_price
    stop_exit = order.stop_loss_price or (
        order.stop_price if order.type in ("stop", "stop_limit") else None
    )
    if order.qty is not None and entry_ref is not None and stop_exit is not None:
        return round(order.qty * abs(entry_ref - stop_exit), 2)
    if order.notional is not None:
        return round(order.notional, 2)
    if order.qty is not None and entry_ref is not None:
        return round(order.qty * entry_ref, 2)
    return None


def order_fingerprint(
    order: NewOrder, *, account_id: str, mode: str, estimated_max_loss: float | None
) -> str:
    """Canonical string of exactly what a token is bound to (§13)."""
    payload = {
        "symbol": order.symbol.upper(),
        "side": order.side,
        "type": order.type,
        "time_in_force": order.time_in_force,
        "qty": order.qty,
        "notional": order.notional,
        "limit_price": order.limit_price,
        "stop_price": order.stop_price,
        "trail_percent": order.trail_percent,
        "trail_price": order.trail_price,
        "account_id": account_id,
        "mode": mode,
        "estimated_max_loss": estimated_max_loss,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _sign(jti: str, expiry: int, fingerprint: str, secret: str) -> str:
    msg = f"{jti}.{expiry}.{fingerprint}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def _now_naive() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def mint_token(
    db: Session,
    order: NewOrder,
    *,
    account_id: str,
    mode: str,
    estimated_max_loss: float | None,
    secret: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: dt.datetime | None = None,
) -> tuple[str, dt.datetime]:
    if not secret:
        raise OrderConfirmationError("ORDER_CONFIRMATION_SECRET is not configured.")
    now = now or dt.datetime.now(dt.timezone.utc)
    expires_at = now + dt.timedelta(seconds=ttl_seconds)
    expiry = int(expires_at.timestamp())
    jti = secrets.token_hex(16)
    fingerprint = order_fingerprint(
        order, account_id=account_id, mode=mode, estimated_max_loss=estimated_max_loss
    )
    sig = _sign(jti, expiry, fingerprint, secret)

    db.add(
        models.OrderConfirmationToken(
            jti=jti,
            fingerprint=fingerprint,
            account_id=account_id,
            mode=mode,
            estimated_max_loss=estimated_max_loss,
            expires_at=expires_at.replace(tzinfo=None),
            used_at=None,
            created_at=now.replace(tzinfo=None),
        )
    )
    db.commit()
    return f"{jti}.{expiry}.{sig}", expires_at


def verify_and_consume(
    db: Session,
    token: str | None,
    order: NewOrder,
    *,
    account_id: str,
    mode: str,
    estimated_max_loss: float | None,
    secret: str,
    now: dt.datetime | None = None,
) -> None:
    """Raise OrderConfirmationError unless `token` is a valid, unexpired,
    unused confirmation for exactly these order details. Marks it used."""
    if not secret:
        raise OrderConfirmationError("ORDER_CONFIRMATION_SECRET is not configured.")
    if not token:
        raise OrderConfirmationError("An order confirmation token is required.")
    try:
        jti, expiry_str, sig = token.split(".", 2)
        expiry = int(expiry_str)
    except (ValueError, AttributeError):
        raise OrderConfirmationError("Malformed confirmation token.")

    fingerprint = order_fingerprint(
        order, account_id=account_id, mode=mode, estimated_max_loss=estimated_max_loss
    )
    expected = _sign(jti, expiry, fingerprint, secret)
    if not hmac.compare_digest(expected, sig):
        raise OrderConfirmationError(
            "Confirmation token does not match the submitted order details."
        )

    row = db.query(models.OrderConfirmationToken).filter_by(jti=jti).one_or_none()
    if row is None:
        raise OrderConfirmationError("Unknown confirmation token.")
    if row.used_at is not None:
        raise OrderConfirmationError("Confirmation token has already been used.")
    if row.fingerprint != fingerprint:
        raise OrderConfirmationError(
            "Confirmation token does not match the submitted order details."
        )

    now = now or dt.datetime.now(dt.timezone.utc)
    if now.timestamp() > expiry:
        raise OrderConfirmationError("Confirmation token has expired.")

    row.used_at = now.replace(tzinfo=None)
    db.commit()
