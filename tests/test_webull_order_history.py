"""WebullBroker.get_order_history mapping, pagination, and filters, plus the
_map_webull_orders / _normalize_order_status helpers. Built offline."""
from unittest.mock import MagicMock

from catalystiq.providers.broker import (
    WebullBroker,
    _map_webull_orders,
    _normalize_order_status,
)


def make_broker(trade_client=None) -> WebullBroker:
    broker = object.__new__(WebullBroker)
    broker._account_id = "test-account"
    broker._market = "US"
    broker._trade_client = trade_client or MagicMock()
    return broker


def fake_response(status_code=200, json_body=None, text=""):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body if json_body is not None else {}
    response.text = text
    return response


# A filled VOO buy, in the shape Webull's /openapi/trade/order/history returns
# (numbers as strings; combo_type NORMAL is a top-level single-leg order).
VOO_FILLED = {
    "order_id": "ORD-1",
    "client_order_id": "COID-1",
    "symbol": "VOO",
    "side": "BUY",
    "order_type": "MARKET",
    "time_in_force": "DAY",
    "status": "FILLED",
    "total_quantity": "1",
    "filled_quantity": "1",
    "avg_fill_price": "684.65",
    "filled_amount": "684.65",
    "commission": "0",
    "create_time": "2026-07-18T14:30:00Z",
    "update_time": "2026-07-18T14:30:01Z",
}


def test_normalize_order_status_variants():
    assert _normalize_order_status("FILLED") == "filled"
    assert _normalize_order_status("PARTIAL FILLED") == "partially_filled"
    assert _normalize_order_status("PARTIAL_FILLED") == "partially_filled"
    assert _normalize_order_status("SUBMITTED") == "open"
    assert _normalize_order_status("CANCELLED") == "cancelled"
    assert _normalize_order_status("REJECTED") == "failed"
    assert _normalize_order_status("something-else") == "unknown"
    assert _normalize_order_status("") == "unknown"


def test_map_orders_maps_core_fields():
    [rec] = _map_webull_orders({"orders": [VOO_FILLED]})
    assert rec.order_id == "ORD-1"
    assert rec.client_order_id == "COID-1"
    assert rec.symbol == "VOO"
    assert rec.side == "BUY"
    assert rec.status == "filled"
    assert rec.status_raw == "FILLED"
    assert rec.filled_qty == "1"
    assert rec.avg_fill_price == "684.65"
    assert rec.filled_amount == "684.65"
    assert rec.is_filled is True
    assert rec.raw == VOO_FILLED


def test_map_orders_camelcase_and_alias_fields():
    [rec] = _map_webull_orders(
        [
            {
                "orderId": "O2",
                "clientOrderId": "C2",
                "symbol": "AAPL",
                "side": "SELL",
                "orderType": "LIMIT",
                "status": "PARTIAL FILLED",
                "quantity": "10",
                "filledQuantity": "4",
                "avgFillPrice": "190.10",
                "filledAmount": "760.40",
            }
        ]
    )
    assert rec.order_id == "O2"
    assert rec.client_order_id == "C2"
    assert rec.order_type == "LIMIT"
    assert rec.status == "partially_filled"
    assert rec.total_qty == "10"
    assert rec.filled_qty == "4"
    assert rec.avg_fill_price == "190.10"
    assert rec.is_filled is True


def test_map_orders_combo_leg_fallback():
    # A combo/group order keeps the instrument + fill on a nested leg.
    [rec] = _map_webull_orders(
        [
            {
                "order_id": "G1",
                "client_order_id": "GC1",
                "combo_type": "BRACKET",
                "status": "FILLED",
                "items": [
                    {
                        "symbol": "MSFT",
                        "side": "BUY",
                        "filled_quantity": "3",
                        "avg_fill_price": "410.00",
                    }
                ],
            }
        ]
    )
    assert rec.symbol == "MSFT"
    assert rec.side == "BUY"
    assert rec.filled_qty == "3"
    assert rec.avg_fill_price == "410.00"


def test_get_order_history_single_page():
    tc = MagicMock()
    tc.order_v3.get_order_history.return_value = fake_response(json_body={"orders": [VOO_FILLED]})
    broker = make_broker(tc)

    records = broker.get_order_history(page_size=100)

    assert len(records) == 1
    assert records[0].symbol == "VOO"
    # First page uses no cursor.
    _, kwargs = tc.order_v3.get_order_history.call_args
    assert kwargs["last_client_order_id"] is None
    assert kwargs["page_size"] == 100


def test_get_order_history_paginates_until_short_page():
    tc = MagicMock()
    page1 = {"orders": [dict(VOO_FILLED, order_id=f"O{i}", client_order_id=f"C{i}") for i in range(2)]}
    page2 = {"orders": [dict(VOO_FILLED, order_id="O9", client_order_id="C9")]}  # short -> stop
    tc.order_v3.get_order_history.side_effect = [
        fake_response(json_body=page1),
        fake_response(json_body=page2),
    ]
    broker = make_broker(tc)

    records = broker.get_order_history(page_size=2)

    # 2 from page1 + 1 from page2 (short page ends pagination).
    assert [r.client_order_id for r in records] == ["C0", "C1", "C9"]
    assert tc.order_v3.get_order_history.call_count == 2
    # Second call advanced the cursor to page1's last client_order_id.
    _, kwargs = tc.order_v3.get_order_history.call_args_list[1]
    assert kwargs["last_client_order_id"] == "C1"


def test_get_order_history_dedupes_boundary_row():
    tc = MagicMock()
    # A full page whose last row repeats as the first row of the next page.
    page1 = {"orders": [
        dict(VOO_FILLED, order_id="O0", client_order_id="C0"),
        dict(VOO_FILLED, order_id="O1", client_order_id="C1"),
    ]}
    page2 = {"orders": [
        dict(VOO_FILLED, order_id="O1", client_order_id="C1"),  # duplicate boundary
    ]}
    tc.order_v3.get_order_history.side_effect = [
        fake_response(json_body=page1),
        fake_response(json_body=page2),
    ]
    broker = make_broker(tc)

    records = broker.get_order_history(page_size=2)

    assert [r.client_order_id for r in records] == ["C0", "C1"]


def test_get_order_history_symbol_and_filled_filters():
    tc = MagicMock()
    body = {"orders": [
        VOO_FILLED,
        dict(VOO_FILLED, order_id="O2", client_order_id="C2", symbol="AAPL"),
        dict(VOO_FILLED, order_id="O3", client_order_id="C3", status="CANCELLED",
             filled_quantity="0"),
    ]}
    tc.order_v3.get_order_history.return_value = fake_response(json_body=body)
    broker = make_broker(tc)

    voo = broker.get_order_history(symbol="voo")
    assert {r.symbol for r in voo} == {"VOO"}

    filled = broker.get_order_history(filled_only=True)
    assert all(r.is_filled for r in filled)
    assert "C3" not in {r.client_order_id for r in filled}


def test_get_order_history_stops_at_max_pages():
    tc = MagicMock()
    # Always returns a full, all-new page -> would loop forever without the cap.
    counter = {"n": 0}

    def _resp(*args, **kwargs):
        counter["n"] += 1
        n = counter["n"]
        return fake_response(json_body={"orders": [
            dict(VOO_FILLED, order_id=f"O{n}a", client_order_id=f"C{n}a"),
            dict(VOO_FILLED, order_id=f"O{n}b", client_order_id=f"C{n}b"),
        ]})

    tc.order_v3.get_order_history.side_effect = _resp
    broker = make_broker(tc)

    records = broker.get_order_history(page_size=2, max_pages=3)

    assert tc.order_v3.get_order_history.call_count == 3
    assert len(records) == 6
