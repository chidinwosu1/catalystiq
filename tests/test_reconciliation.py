"""reconcile_order / find_order: the read-only order-vs-position-vs-balance
reconciliation used by the CLI and the /paper/reconcile endpoint."""
from catalystiq.reconciliation import find_order, reconcile_order
from catalystiq.schemas.broker import AccountInfo, OrderRecord, Position


def voo_order(**over) -> OrderRecord:
    base = dict(
        order_id="ORD-1",
        client_order_id="COID-1",
        symbol="VOO",
        side="BUY",
        order_type="MARKET",
        status="filled",
        status_raw="FILLED",
        total_qty="1",
        filled_qty="1",
        avg_fill_price="684.65",
        filled_amount="684.65",
        commission="0",
        updated_at="2026-07-18T14:30:01Z",
    )
    base.update(over)
    return OrderRecord(**base)


def voo_position(**over) -> Position:
    base = dict(
        symbol="VOO",
        side="long",
        qty="1",
        avg_entry_price="684.65",
        market_value="682.91",
        cost_basis="684.65",
        unrealized_pl="-1.74",
        unrealized_plpc="-0.0025",
        current_price="682.91",
        change_today="-1.74",
    )
    base.update(over)
    return Position(**base)


def account(buying_power="3999310.16") -> AccountInfo:
    return AccountInfo(
        status="ACTIVE",
        currency="USD",
        cash="999315.35",
        buying_power=buying_power,
        portfolio_value="999998.27",
        equity="999998.27",
        last_equity="1000000.00",
        trading_blocked=False,
        account_blocked=False,
        pattern_day_trader=False,
    )


def _check(recon, name):
    return next(c for c in recon.checks if c.name == name)


def test_clean_buy_reconciles_ok():
    recon = reconcile_order("APIID-ABC123", voo_order(), [voo_position()], account())

    assert recon.ok is True
    assert recon.symbol == "VOO"
    assert recon.position is not None
    assert _check(recon, "order_filled").ok
    assert _check(recon, "filled_amount_consistent").ok
    assert _check(recon, "position_present").ok
    assert _check(recon, "cost_basis_consistent").ok
    # No baseline -> BP check is informational (passes) with modeled impact.
    bp = recon.buying_power
    assert bp.actual_change is None
    assert float(bp.expected_change) == -684.65  # -(1 * 684.65 + 0)


def test_missing_position_fails_position_present():
    recon = reconcile_order("id", voo_order(), [], account())
    assert recon.ok is False
    assert _check(recon, "position_present").ok is False


def test_cost_basis_divergence_fails():
    # Position avg entry far from the fill price.
    recon = reconcile_order(
        "id", voo_order(), [voo_position(avg_entry_price="700.00")], account()
    )
    assert _check(recon, "cost_basis_consistent").ok is False
    assert recon.ok is False


def test_filled_amount_inconsistent_fails():
    recon = reconcile_order(
        "id", voo_order(filled_amount="999.99"), [voo_position()], account()
    )
    assert _check(recon, "filled_amount_consistent").ok is False


def test_missing_filled_amount_is_tolerated():
    recon = reconcile_order(
        "id", voo_order(filled_amount="0"), [voo_position()], account()
    )
    # Broker omitted filled_amount -> nothing to contradict qty*price.
    assert _check(recon, "filled_amount_consistent").ok is True


def test_buying_power_delta_direction_buy():
    # Buy should not increase BP: baseline higher than current -> negative delta, ok.
    recon = reconcile_order(
        "id",
        voo_order(),
        [voo_position()],
        account(buying_power="3998625.51"),
        baseline_buying_power="3999310.16",
    )
    bp = recon.buying_power
    assert bp.baseline == "3999310.16"
    assert float(bp.actual_change) < 0
    assert _check(recon, "buying_power_change").ok is True


def test_buying_power_wrong_direction_fails():
    # Buy but BP went UP vs baseline -> direction check fails.
    recon = reconcile_order(
        "id",
        voo_order(),
        [voo_position()],
        account(buying_power="4000000.00"),
        baseline_buying_power="3999310.16",
    )
    assert _check(recon, "buying_power_change").ok is False
    assert recon.ok is False


def test_sell_expected_change_is_positive_cash():
    recon = reconcile_order(
        "id",
        voo_order(side="SELL", commission="1.00"),
        [],  # position closed
        account(),
    )
    # +qty*price - commission = 684.65 - 1.00
    assert float(recon.buying_power.expected_change) == 683.65
    # A filled SELL doesn't assert position_present.
    assert all(c.name != "position_present" for c in recon.checks)


def test_find_order_by_client_order_id():
    orders = [voo_order(client_order_id="A"), voo_order(client_order_id="B", symbol="AAPL")]
    assert find_order(orders, client_order_id="B").symbol == "AAPL"
    assert find_order(orders, client_order_id="ZZZ") is None


def test_find_order_by_symbol_prefers_filled_latest():
    orders = [
        voo_order(client_order_id="old", status="cancelled", status_raw="CANCELLED",
                  filled_qty="0", updated_at="2026-07-01T00:00:00Z"),
        voo_order(client_order_id="new", updated_at="2026-07-18T00:00:00Z"),
    ]
    picked = find_order(orders, symbol="VOO")
    assert picked.client_order_id == "new"


def test_find_order_symbol_no_match():
    assert find_order([voo_order()], symbol="TSLA") is None
