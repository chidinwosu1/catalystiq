"""Read-only order reconciliation (§13).

`reconcile_order` cross-checks a single order's fill against the resulting
position and account balance and returns an `OrderReconciliation`. It is a pure
function over already-fetched data: it performs NO broker calls and therefore
can never place, modify, or cancel an order. The caller is responsible for
fetching the order history / positions / balance (all read-only) and for
redacting the account id before display.

What it can and cannot verify from a single post-trade snapshot:

  * Order side of the reconciliation (status, filled qty, avg price, filled
    amount) is verified for internal consistency.
  * The resulting position is checked for cost-basis consistency with the
    fill (a filled buy should be reflected in the position's average entry
    price), not just presence.
  * Buying power: a lone post-trade snapshot has no "before" value, so the
    modeled cash impact of the fill is always reported as `expected_change`,
    and an `actual_change` is computed only when the caller supplies a
    pre-trade `baseline_buying_power`. Even then the check is direction-based,
    because a margin account's buying-power move is a plan-specific multiple of
    the cash leg and must not be asserted as an equality.
"""
from __future__ import annotations

from catalystiq.schemas.broker import (
    AccountInfo,
    BuyingPowerReconciliation,
    OrderReconciliation,
    OrderRecord,
    Position,
    ReconciliationCheck,
)


def _f(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _fmt(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".") if value else "0"


def _is_buy(order: OrderRecord) -> bool:
    return order.side.strip().upper() in {"BUY", "B", "LONG"}


def find_order(
    orders: list[OrderRecord],
    *,
    symbol: str | None = None,
    client_order_id: str | None = None,
    order_id: str | None = None,
) -> OrderRecord | None:
    """Pick the order to reconcile. An explicit client_order_id/order_id wins;
    otherwise the most recently-updated FILLED (or partially filled) order for
    `symbol`, falling back to the most recent order for that symbol."""
    if client_order_id:
        for o in orders:
            if o.client_order_id == client_order_id:
                return o
        return None
    if order_id:
        for o in orders:
            if o.order_id == order_id:
                return o
        return None
    if not symbol:
        return None
    want = symbol.strip().upper()
    matches = [o for o in orders if o.symbol.upper() == want]
    if not matches:
        return None
    filled = [o for o in matches if o.is_filled]
    pool = filled or matches
    # `updated_at` is a lexically-sortable timestamp string when present; the
    # broker already returns history newest-first, so a stable max on
    # updated_at (empty sorts first) preserves that ordering.
    return max(pool, key=lambda o: o.updated_at or o.created_at or "")


def reconcile_order(
    account_id: str,
    order: OrderRecord,
    positions: list[Position],
    account: AccountInfo,
    *,
    baseline_buying_power: str | None = None,
    price_tolerance_pct: float = 0.5,
) -> OrderReconciliation:
    """Reconcile `order` against `positions` and `account`. Pure/read-only."""
    checks: list[ReconciliationCheck] = []

    filled_qty = _f(order.filled_qty)
    avg_price = _f(order.avg_fill_price)
    commission = _f(order.commission)
    computed_notional = filled_qty * avg_price

    # 1) The order actually put shares on the book.
    checks.append(
        ReconciliationCheck(
            name="order_filled",
            ok=order.is_filled,
            expected="filled or partially_filled with filled_qty > 0",
            actual=f"status={order.status} ({order.status_raw or 'n/a'}), "
            f"filled_qty={order.filled_qty}",
            detail="Order reached a (partially) filled state."
            if order.is_filled
            else "Order is not in a filled state; downstream checks are informational.",
        )
    )

    # 2) The order record is internally consistent: filled_amount ~= qty*price.
    reported_amount = _f(order.filled_amount)
    amount_tol = max(0.01, abs(computed_notional) * price_tolerance_pct / 100.0)
    amount_ok = (
        abs(reported_amount - computed_notional) <= amount_tol
        if reported_amount
        else True  # broker omitted filled_amount; nothing to contradict
    )
    checks.append(
        ReconciliationCheck(
            name="filled_amount_consistent",
            ok=amount_ok,
            expected=f"filled_qty*avg_price={_fmt(computed_notional)}",
            actual=f"filled_amount={order.filled_amount or 'n/a'}",
            detail="Order's reported fill amount matches qty x price."
            if amount_ok
            else "Reported fill amount disagrees with qty x price beyond tolerance.",
        )
    )

    # 3) Resulting position and its cost-basis consistency with the fill.
    want = order.symbol.strip().upper()
    position = next((p for p in positions if p.symbol.strip().upper() == want), None)

    if order.is_filled and _is_buy(order):
        pos_present_ok = position is not None
        checks.append(
            ReconciliationCheck(
                name="position_present",
                ok=pos_present_ok,
                expected=f"an open position in {want}",
                actual="present" if pos_present_ok else "absent",
                detail="A filled buy is reflected by an open position."
                if pos_present_ok
                else "Filled buy but no matching open position was found.",
            )
        )

    if position is not None:
        pos_avg = _f(position.avg_entry_price)
        if avg_price > 0 and pos_avg > 0:
            rel = abs(pos_avg - avg_price) / avg_price * 100.0
            basis_ok = rel <= price_tolerance_pct
            checks.append(
                ReconciliationCheck(
                    name="cost_basis_consistent",
                    ok=basis_ok,
                    expected=f"position avg entry ~= fill avg price {_fmt(avg_price)} "
                    f"(<= {price_tolerance_pct}%)",
                    actual=f"position avg entry={position.avg_entry_price} ({rel:.3f}% diff)",
                    detail="Resulting position's average entry price reflects the fill."
                    if basis_ok
                    else "Position average entry price diverges from the fill price - "
                    "other fills may contribute to this position.",
                )
            )
        # Informational: does this single fill fully explain the position size?
        pos_qty = abs(_f(position.qty))
        checks.append(
            ReconciliationCheck(
                name="position_quantity",
                ok=pos_qty >= filled_qty - 1e-9,
                expected=f"position qty >= this fill's {order.filled_qty}",
                actual=f"position qty={position.qty}",
                detail="Position holds at least this order's filled quantity."
                if pos_qty >= filled_qty - 1e-9
                else "Position quantity is smaller than this fill - it may have been "
                "partly closed by later orders.",
            )
        )

    # 4) Buying-power / cash impact of the fill.
    if _is_buy(order):
        expected_change = -(computed_notional + commission)
    else:
        expected_change = computed_notional - commission

    current_bp = _f(account.buying_power)
    bp = BuyingPowerReconciliation(
        current=account.buying_power,
        baseline=baseline_buying_power,
        expected_change=_fmt(expected_change),
        note=(
            "expected_change is the fill's modeled CASH impact "
            "(qty x price +/- commission). For a margin account the buying-power "
            "move is a plan-specific multiple of this; the check below is "
            "direction-based, not an equality."
        ),
    )
    if baseline_buying_power is not None:
        actual_change = current_bp - _f(baseline_buying_power)
        bp.actual_change = _fmt(actual_change)
        # A buy must not increase buying power; a sell must not decrease it.
        if _is_buy(order):
            bp_ok = actual_change <= 1e-6
            direction = "not increase"
        else:
            bp_ok = actual_change >= -1e-6
            direction = "not decrease"
        checks.append(
            ReconciliationCheck(
                name="buying_power_change",
                ok=bp_ok,
                expected=f"a {order.side or 'trade'} should {direction} buying power",
                actual=f"actual_change={bp.actual_change} (baseline={baseline_buying_power} "
                f"-> current={account.buying_power})",
                detail="Buying-power moved in the expected direction for this fill."
                if bp_ok
                else "Buying-power moved against the expected direction for this fill.",
            )
        )
    else:
        checks.append(
            ReconciliationCheck(
                name="buying_power_change",
                ok=True,
                expected=f"modeled cash impact {bp.expected_change}",
                actual=f"current buying_power={account.buying_power}",
                detail="No pre-trade baseline supplied, so only the modeled cash impact "
                "is reported (pass baseline_buying_power to compute the actual delta).",
            )
        )

    return OrderReconciliation(
        account_id=account_id,
        symbol=order.symbol,
        order=order,
        position=position,
        buying_power=bp,
        checks=checks,
        ok=all(c.ok for c in checks),
    )
