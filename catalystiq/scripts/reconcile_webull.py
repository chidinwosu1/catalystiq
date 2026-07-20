"""Read-only Webull sandbox reconciliation CLI (§13).

Confirms an account against the Webull account list, then pulls order history,
positions, and balance and reconciles one order against the resulting position
and buying power. It is STRICTLY read-only: it only ever calls the read paths
(get_account_list / get_order_history / get_positions / get_account) and never
places, previews, modifies, or cancels an order.

Credentials are read from the environment / .env exactly like the app (they are
never passed on the command line and never printed). Account ids and numbers are
redacted in the output by default (show `--help` for `--no-redact`, which is for
local debugging only).

Usage (run where the WEBULL_* env vars are configured, e.g. a Render shell):

    python -m catalystiq.scripts.reconcile_webull --account DEM34946 --symbol VOO

    # widen the history window (Webull defaults to the last 7 days):
    python -m catalystiq.scripts.reconcile_webull --symbol VOO \
        --start 2026-06-01 --end 2026-07-20

    # compute the actual buying-power delta from a pre-trade snapshot:
    python -m catalystiq.scripts.reconcile_webull --symbol VOO \
        --baseline-buying-power 3999310.16

    # machine-readable (already redacted unless --no-redact):
    python -m catalystiq.scripts.reconcile_webull --symbol VOO --json
"""
from __future__ import annotations

import argparse
import json
import sys

from catalystiq.providers.broker import BrokerError, get_broker_provider
from catalystiq.reconciliation import find_order, reconcile_order
from catalystiq.schemas.broker import OrderReconciliation


def redact_id(value: str, keep: int = 4) -> str:
    """Mask an account id/number, revealing only the last `keep` chars so a
    human can still recognize it (e.g. "DEM34946" -> "****4946"). Empty in,
    empty out."""
    v = (value or "").strip()
    if not v:
        return ""
    if len(v) <= keep:
        return "*" * len(v)
    return "*" * (len(v) - keep) + v[-keep:]


def _redact_recon(recon: OrderReconciliation) -> dict:
    """JSON-safe, redacted view of a reconciliation: mask the account id and
    drop the raw provider blobs (which can echo ids)."""
    data = recon.model_dump(mode="json")
    data["account_id"] = redact_id(data.get("account_id", ""))
    if isinstance(data.get("order"), dict):
        data["order"].pop("raw", None)
    if isinstance(data.get("position"), dict):
        data["position"].pop("raw", None)
    return data


def _print_report(
    recon: OrderReconciliation,
    *,
    account_ref: str,
    resolved_id: str,
    configured_id: str,
    redact: bool,
) -> None:
    def show(value: str) -> str:
        return redact_id(value) if redact else value

    o = recon.order
    p = recon.position
    bp = recon.buying_power

    print("=" * 66)
    print("  Webull sandbox reconciliation (READ-ONLY)")
    print("=" * 66)
    print("\nAccount")
    print(f"  requested reference : {show(account_ref) if account_ref else '(configured)'}")
    print(f"  resolved account id : {show(resolved_id)}")
    if configured_id and resolved_id and configured_id != resolved_id:
        print(f"  configured id       : {show(configured_id)}  !! MISMATCH")
        print("  WARNING: the resolved account differs from the configured account id;")
        print("  order/position/balance below are for the CONFIGURED account.")
    else:
        print("  matches configured  : yes")

    print("\nOrder")
    print(f"  symbol              : {o.symbol}")
    print(f"  side / type         : {o.side or 'n/a'} / {o.order_type or 'n/a'}")
    print(f"  status              : {o.status} ({o.status_raw or 'n/a'})")
    print(f"  filled qty          : {o.filled_qty} of {o.total_qty}")
    print(f"  avg fill price      : {o.avg_fill_price}")
    print(f"  filled amount       : {o.filled_amount}")
    print(f"  commission          : {o.commission}")
    print(f"  client_order_id     : {show(o.client_order_id)}")

    print("\nResulting position")
    if p is None:
        print("  (no open position for this symbol)")
    else:
        print(f"  qty                 : {p.qty} ({p.side})")
        print(f"  avg entry price     : {p.avg_entry_price}")
        print(f"  market value        : {p.market_value}")
        print(f"  cost basis          : {p.cost_basis}")
        print(f"  unrealized p/l      : {p.unrealized_pl} ({p.unrealized_plpc})")

    print("\nBuying power")
    print(f"  current             : {bp.current}")
    print(f"  baseline (pre-trade): {bp.baseline if bp.baseline is not None else 'n/a'}")
    print(f"  expected change     : {bp.expected_change}")
    print(f"  actual change       : {bp.actual_change if bp.actual_change is not None else 'n/a'}")
    print(f"  note                : {bp.note}")

    print("\nChecks")
    for c in recon.checks:
        mark = "PASS" if c.ok else "FAIL"
        print(f"  [{mark}] {c.name}: {c.detail}")
        if not c.ok:
            print(f"         expected: {c.expected}")
            print(f"         actual  : {c.actual}")

    print("\n" + "-" * 66)
    print(f"  RESULT: {'OK - reconciled' if recon.ok else 'DISCREPANCY - see failed checks'}")
    print("-" * 66)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m catalystiq.scripts.reconcile_webull",
        description="Read-only Webull sandbox reconciliation. Never places or cancels orders.",
    )
    parser.add_argument(
        "--account",
        default="",
        help="Human-facing account number (e.g. DEM34946) or API account id to confirm. "
        "Defaults to the configured WEBULL_ACCOUNT_ID.",
    )
    parser.add_argument("--symbol", default="VOO", help="Symbol to reconcile (default: VOO).")
    parser.add_argument("--client-order-id", default=None, help="Reconcile this exact order.")
    parser.add_argument("--order-id", default=None, help="Reconcile this exact order id.")
    parser.add_argument("--start", default=None, help="History start date yyyy-MM-dd.")
    parser.add_argument("--end", default=None, help="History end date yyyy-MM-dd.")
    parser.add_argument(
        "--baseline-buying-power",
        default=None,
        help="Pre-trade buying power, to compute the actual delta.",
    )
    parser.add_argument("--json", action="store_true", help="Emit redacted JSON instead of text.")
    parser.add_argument(
        "--no-redact",
        action="store_true",
        help="Do NOT redact account ids (local debugging only).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    redact = not args.no_redact

    try:
        broker = get_broker_provider()
    except BrokerError as exc:
        print(f"error: Webull broker is not configured: {exc}", file=sys.stderr)
        return 2

    configured_id = getattr(broker, "_account_id", "")
    account_ref = args.account or configured_id

    try:
        # 1) Confirm the account via the account list.
        resolved_id = broker.find_account_id(account_ref) if account_ref else configured_id

        # 2) Read-only pulls: order history, positions, balance.
        orders = broker.get_order_history(
            start_date=args.start,
            end_date=args.end,
            symbol=args.symbol if not (args.client_order_id or args.order_id) else None,
        )
        order = find_order(
            orders,
            symbol=args.symbol,
            client_order_id=args.client_order_id,
            order_id=args.order_id,
        )
        if order is None:
            target = args.client_order_id or args.order_id or args.symbol
            print(
                f"error: no matching order for {target!r} in the requested window "
                "(Webull defaults to the last 7 days; try --start/--end).",
                file=sys.stderr,
            )
            return 3

        positions = broker.get_positions()
        account = broker.get_account()
    except BrokerError as exc:
        print(f"error: read-only Webull call failed: {exc}", file=sys.stderr)
        return 2

    recon = reconcile_order(
        account_id=configured_id,
        order=order,
        positions=positions,
        account=account,
        baseline_buying_power=args.baseline_buying_power,
    )

    if args.json:
        payload = _redact_recon(recon) if redact else recon.model_dump(mode="json")
        payload["_resolved_account_id"] = redact_id(resolved_id) if redact else resolved_id
        print(json.dumps(payload, indent=2))
    else:
        _print_report(
            recon,
            account_ref=args.account,
            resolved_id=resolved_id,
            configured_id=configured_id,
            redact=redact,
        )

    return 0 if recon.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
