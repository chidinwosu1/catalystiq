"""Order-submission gating (§13): disabled by default, single-use tokens
bound to exact order details, live unavailable, and the read-only connection
test. Uses dependency overrides for settings and the broker (no live calls)."""
import datetime as dt

import pytest

from catalystiq.config import Settings, get_settings
from catalystiq.orders import (
    OrderConfirmationError,
    estimate_max_loss,
    mint_token,
    verify_and_consume,
)
from catalystiq.providers.broker import get_broker_provider
from catalystiq.schemas.broker import NewOrder


class FakeBroker:
    def __init__(self):
        self.submitted = []

    def submit_order(self, order):
        self.submitted.append(order)
        return {"id": "fake-1", "status": "accepted"}

    def get_orders(self):
        return []

    def connection_test(self):
        return {"provider": "webull", "ok": True, "detail": "reachable (read-only)"}


def _enabled_settings():
    return Settings(
        trading_mode="paper",
        enable_paper_order_submission=True,
        order_confirmation_secret="test-secret",
        action_api_key="ci-test-key",
    )


def _order():
    return NewOrder(symbol="AAPL", side="buy", type="limit", qty=10, limit_price=100.0,
                    stop_loss_price=90.0)


# --- unit: token binding + single use ----------------------------------

def test_estimate_max_loss_with_stop():
    # 10 shares, entry 100, stop 90 -> 100 max loss.
    assert estimate_max_loss(_order()) == 100.0


def test_token_roundtrip_and_single_use(test_db_session):
    db = test_db_session
    order = _order()
    ml = estimate_max_loss(order)
    token, _ = mint_token(db, order, account_id="acct-1", mode="paper",
                          estimated_max_loss=ml, secret="s")
    # Valid once.
    verify_and_consume(db, token, order, account_id="acct-1", mode="paper",
                       estimated_max_loss=ml, secret="s")
    # Replays are rejected (single use).
    with pytest.raises(OrderConfirmationError):
        verify_and_consume(db, token, order, account_id="acct-1", mode="paper",
                           estimated_max_loss=ml, secret="s")


def test_token_rejects_param_change(test_db_session):
    db = test_db_session
    order = _order()
    ml = estimate_max_loss(order)
    token, _ = mint_token(db, order, account_id="acct-1", mode="paper",
                          estimated_max_loss=ml, secret="s")
    tampered = order.model_copy(update={"qty": 999})
    with pytest.raises(OrderConfirmationError):
        verify_and_consume(db, token, tampered, account_id="acct-1", mode="paper",
                           estimated_max_loss=estimate_max_loss(tampered), secret="s")


def test_token_rejects_wrong_account(test_db_session):
    db = test_db_session
    order = _order()
    ml = estimate_max_loss(order)
    token, _ = mint_token(db, order, account_id="acct-1", mode="paper",
                          estimated_max_loss=ml, secret="s")
    with pytest.raises(OrderConfirmationError):
        verify_and_consume(db, token, order, account_id="acct-OTHER", mode="paper",
                           estimated_max_loss=ml, secret="s")


def test_expired_token_rejected(test_db_session):
    db = test_db_session
    order = _order()
    ml = estimate_max_loss(order)
    past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
    token, _ = mint_token(db, order, account_id="a", mode="paper", estimated_max_loss=ml,
                          secret="s", ttl_seconds=1, now=past)
    with pytest.raises(OrderConfirmationError):
        verify_and_consume(db, token, order, account_id="a", mode="paper",
                           estimated_max_loss=ml, secret="s")


# --- endpoint: gate behavior -------------------------------------------

def _client_with(client, settings, broker):
    from catalystiq.main import app

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_broker_provider] = lambda: broker
    return client


def test_submission_disabled_by_default(client):
    # Default settings: paper submission off -> 403. (Override the broker too
    # so the gate - not broker construction - is what responds.)
    from catalystiq.main import app

    app.dependency_overrides[get_settings] = lambda: Settings(action_api_key="ci-test-key")
    app.dependency_overrides[get_broker_provider] = lambda: FakeBroker()
    body = {"order": _order().model_dump(mode="json"), "account_id": "a", "confirmation_token": "x"}
    resp = client.post("/paper/orders", json=body)
    assert resp.status_code == 403
    assert "disabled" in resp.json()["detail"].lower()
    app.dependency_overrides.pop(get_settings, None)
    app.dependency_overrides.pop(get_broker_provider, None)


def test_live_mode_refused_even_if_flag_set(client):
    from catalystiq.main import app

    settings = Settings(trading_mode="live", enable_live_order_submission=True,
                        order_confirmation_secret="s", action_api_key="ci-test-key")
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_broker_provider] = lambda: FakeBroker()
    body = {"order": _order().model_dump(mode="json"), "account_id": "a", "confirmation_token": "x"}
    resp = client.post("/paper/orders", json=body)
    assert resp.status_code == 403
    assert "live" in resp.json()["detail"].lower()
    app.dependency_overrides.pop(get_settings, None)
    app.dependency_overrides.pop(get_broker_provider, None)


def test_confirm_then_submit_happy_path(client):
    from catalystiq.main import app

    broker = FakeBroker()
    _client_with(client, _enabled_settings(), broker)
    order_json = _order().model_dump(mode="json")

    confirm = client.post("/paper/orders/confirm",
                          json={"order": order_json, "account_id": "acct-1", "confirmation_token": ""})
    assert confirm.status_code == 200
    data = confirm.json()
    assert data["review"]["estimated_max_loss"] == 100.0
    assert data["review"]["account_id"] == "acct-1"
    token = data["confirmation_token"]

    submit = client.post("/paper/orders",
                         json={"order": order_json, "account_id": "acct-1", "confirmation_token": token})
    assert submit.status_code == 200
    assert len(broker.submitted) == 1

    # Single-use: the same token can't submit again.
    again = client.post("/paper/orders",
                        json={"order": order_json, "account_id": "acct-1", "confirmation_token": token})
    assert again.status_code == 403
    assert len(broker.submitted) == 1

    app.dependency_overrides.pop(get_settings, None)
    app.dependency_overrides.pop(get_broker_provider, None)


def test_submit_without_token_rejected(client):
    from catalystiq.main import app

    _client_with(client, _enabled_settings(), FakeBroker())
    body = {"order": _order().model_dump(mode="json"), "account_id": "acct-1",
            "confirmation_token": "bogus.123.deadbeef"}
    resp = client.post("/paper/orders", json=body)
    assert resp.status_code == 403
    app.dependency_overrides.pop(get_settings, None)
    app.dependency_overrides.pop(get_broker_provider, None)


def test_connection_test_is_read_only(client, monkeypatch):
    # The endpoint calls get_broker_provider() directly (to report a config
    # failure gracefully rather than 502), so patch the module reference.
    broker = FakeBroker()
    monkeypatch.setattr("catalystiq.routers.broker.get_broker_provider", lambda: broker)
    resp = client.get("/paper/connection-test")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert broker.submitted == []  # never submitted anything
