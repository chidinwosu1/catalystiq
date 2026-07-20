"""WebullBroker.get_account_list / find_account_id, built offline via
object.__new__ (no SDK package required), matching the rest of the suite."""
from unittest.mock import MagicMock

import pytest

from catalystiq.providers.broker import BrokerError, WebullBroker


def make_broker(trade_client=None) -> WebullBroker:
    broker = object.__new__(WebullBroker)
    broker._account_id = "APIID-ABC123"
    broker._market = "US"
    broker._trade_client = trade_client or MagicMock()
    return broker


def fake_response(status_code=200, json_body=None, text=""):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body if json_body is not None else {}
    response.text = text
    return response


# Shape verified against the SDK: account_v2.get_account_list() takes no args
# and hits GET /openapi/account/list.
ACCOUNTS_BODY = {
    "data": [
        {
            "account_id": "APIID-ABC123",
            "account_number": "DEM34946",
            "account_type": "MARGIN",
            "currency": "USD",
            "status": "ACTIVE",
        },
        {
            "account_id": "APIID-XYZ789",
            "account_number": "DEM99999",
            "account_type": "CASH",
            "currency": "USD",
            "status": "ACTIVE",
        },
    ]
}


def test_get_account_list_maps_rows():
    tc = MagicMock()
    tc.account_v2.get_account_list.return_value = fake_response(json_body=ACCOUNTS_BODY)
    broker = make_broker(tc)

    accounts = broker.get_account_list()

    assert len(accounts) == 2
    first = accounts[0]
    assert first.account_id == "APIID-ABC123"
    assert first.account_number == "DEM34946"
    assert first.account_type == "MARGIN"
    assert first.currency == "USD"
    assert first.raw["status"] == "ACTIVE"
    tc.account_v2.get_account_list.assert_called_once_with()


def test_get_account_list_tolerates_bare_list_and_camelcase():
    tc = MagicMock()
    tc.account_v2.get_account_list.return_value = fake_response(
        json_body=[{"accountId": "ID-1", "accountNumber": "DEM11111"}]
    )
    broker = make_broker(tc)

    accounts = broker.get_account_list()

    assert accounts[0].account_id == "ID-1"
    assert accounts[0].account_number == "DEM11111"


def test_find_account_id_matches_account_number():
    tc = MagicMock()
    tc.account_v2.get_account_list.return_value = fake_response(json_body=ACCOUNTS_BODY)
    broker = make_broker(tc)

    assert broker.find_account_id("DEM34946") == "APIID-ABC123"
    assert broker.find_account_id("dem34946") == "APIID-ABC123"  # case-insensitive


def test_find_account_id_matches_api_id():
    tc = MagicMock()
    tc.account_v2.get_account_list.return_value = fake_response(json_body=ACCOUNTS_BODY)
    broker = make_broker(tc)

    assert broker.find_account_id("APIID-XYZ789") == "APIID-XYZ789"


def test_find_account_id_no_match_raises_without_leaking_ids():
    tc = MagicMock()
    tc.account_v2.get_account_list.return_value = fake_response(json_body=ACCOUNTS_BODY)
    broker = make_broker(tc)

    with pytest.raises(BrokerError) as exc:
        broker.find_account_id("DEM00000")
    # Reports the count, never the other account ids.
    assert "DEM00000" in str(exc.value)
    assert "APIID-ABC123" not in str(exc.value)
    assert "DEM34946" not in str(exc.value)


def test_find_account_id_empty_ref_raises():
    broker = make_broker()
    with pytest.raises(BrokerError):
        broker.find_account_id("")


def test_get_account_list_wraps_error_status():
    tc = MagicMock()
    tc.account_v2.get_account_list.return_value = fake_response(status_code=401, text="unauthorized")
    broker = make_broker(tc)

    with pytest.raises(BrokerError, match="401"):
        broker.get_account_list()
