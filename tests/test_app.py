import datetime as dt

from catalystiq.auth import verify_action_key
from catalystiq.db import models
from catalystiq.main import app
from catalystiq.providers.broker import get_broker_provider
from catalystiq.providers.market_data import get_market_data_provider
from catalystiq.schemas.market_data import FundamentalsSnapshot, NewsItem, OHLCVBar, Quote


def test_root_and_health_need_no_auth(client):
    assert client.get("/").status_code == 200
    assert client.get("/health").status_code == 200


def test_broker_construction_failure_returns_clean_502_with_cors(client, monkeypatch):
    """Regression test: get_broker_provider() runs as a FastAPI dependency, so
    a BrokerError raised there (e.g. missing credentials) used to become an
    unhandled 500 that skipped CORSMiddleware - the browser reported it as a
    CORS failure instead of the real "not configured" error. The
    BrokerError exception handler in main.py fixes this. Webull is the
    default/sole broker, so clearing its credentials is what triggers this.
    """
    from catalystiq.config import get_settings

    monkeypatch.setenv("WEBULL_APP_KEY", "")
    monkeypatch.setenv("WEBULL_APP_SECRET", "")
    monkeypatch.setenv("WEBULL_ACCOUNT_ID", "")
    get_settings.cache_clear()
    try:
        response = client.get(
            "/paper/account", headers={"Origin": "http://localhost:5173"}
        )
        assert response.status_code == 502
        assert "not configured" in response.json()["detail"]
        assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    finally:
        get_settings.cache_clear()


def test_unsupported_broker_provider_returns_clean_502_with_cors(client, monkeypatch):
    """BROKER_PROVIDER values other than "webull" are rejected outright -
    no fallback to Alpaca or any other provider, and the same clean-502-
    with-CORS handling applies as for missing credentials."""
    from catalystiq.config import get_settings

    monkeypatch.setenv("BROKER_PROVIDER", "alpaca")
    get_settings.cache_clear()
    try:
        response = client.get(
            "/paper/account", headers={"Origin": "http://localhost:5173"}
        )
        assert response.status_code == 502
        assert "Unsupported BROKER_PROVIDER" in response.json()["detail"]
        assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    finally:
        get_settings.cache_clear()


def test_paper_account_requires_auth():
    from fastapi.testclient import TestClient

    with TestClient(app) as unauth_client:
        r = unauth_client.get("/paper/account")
        assert r.status_code in (401, 403)


def test_paper_account_uses_overridden_broker(client):
    class FakeBroker:
        def get_account(self):
            from catalystiq.schemas.broker import AccountInfo

            return AccountInfo(
                status="ACTIVE",
                currency="USD",
                cash="100",
                buying_power="200",
                portfolio_value="150",
                equity="150",
                last_equity="145",
                trading_blocked=False,
                account_blocked=False,
                pattern_day_trader=False,
            )

    app.dependency_overrides[get_broker_provider] = lambda: FakeBroker()
    try:
        r = client.get("/paper/account")
        assert r.status_code == 200
        assert r.json()["status"] == "ACTIVE"
    finally:
        del app.dependency_overrides[get_broker_provider]


def _bar(date, close):
    return OHLCVBar(date=date, open=close, high=close + 0.5, low=close - 0.5, close=close, volume=1_000_000)


def test_ingest_price_history_validates_and_persists(client):
    days = []
    d = dt.date(2024, 1, 2)
    while len(days) < 10:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    bars = [_bar(day, 100 + i * 0.1) for i, day in enumerate(days)]

    class FakeProvider:
        def get_ohlcv(self, symbol, start, end=None, interval="1d"):
            return bars

        def get_quote(self, symbol):
            return Quote(
                symbol=symbol.upper(),
                price=bars[-1].close,
                previous_close=bars[-1].close,
                as_of=dt.datetime.now(dt.timezone.utc),
            )

        def get_fundamentals(self, symbol):
            raise NotImplementedError

        def get_news(self, symbol, limit=10):
            raise NotImplementedError

    app.dependency_overrides[get_market_data_provider] = lambda: FakeProvider()
    try:
        r = client.post("/market-data/ingest/TEST", params={"days": 30})
        assert r.status_code == 200
        report = r.json()
        assert report["symbol"] == "TEST"
        assert report["bar_count"] == len(bars)
    finally:
        del app.dependency_overrides[get_market_data_provider]


def test_ingest_price_history_is_idempotent_on_rerun(client, test_db_session):
    days = []
    d = dt.date(2024, 1, 2)
    while len(days) < 5:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    bars = [_bar(day, 100) for day in days]

    class FakeProvider:
        def get_ohlcv(self, symbol, start, end=None, interval="1d"):
            return bars

        def get_quote(self, symbol):
            return Quote(
                symbol=symbol.upper(), price=100, previous_close=100, as_of=dt.datetime.now(dt.timezone.utc)
            )

        def get_fundamentals(self, symbol):
            raise NotImplementedError

        def get_news(self, symbol, limit=10):
            raise NotImplementedError

    app.dependency_overrides[get_market_data_provider] = lambda: FakeProvider()
    try:
        client.post("/market-data/ingest/DUP", params={"days": 30})
        client.post("/market-data/ingest/DUP", params={"days": 30})

        ticker = test_db_session.query(models.Ticker).filter_by(symbol="DUP").one()
        row_count = (
            test_db_session.query(models.PriceHistory).filter_by(ticker_id=ticker.id).count()
        )
        assert row_count == len(bars)
    finally:
        del app.dependency_overrides[get_market_data_provider]


def _future_iso(minutes: int = 30) -> str:
    when = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=minutes)
    return when.isoformat()


def test_create_scheduled_order_persists_pending(client):
    payload = {
        "order": {"symbol": "aapl", "side": "buy", "type": "market", "qty": 5},
        "scheduled_at": _future_iso(),
    }
    r = client.post("/paper/scheduled-orders", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "AAPL"
    assert body["status"] == "pending"
    assert body["order"]["qty"] == 5


def test_create_scheduled_order_rejects_past_time(client):
    payload = {
        "order": {"symbol": "aapl", "side": "buy", "type": "market", "qty": 5},
        "scheduled_at": _future_iso(-30),
    }
    r = client.post("/paper/scheduled-orders", json=payload)
    assert r.status_code == 422


def test_list_scheduled_orders_returns_created(client):
    payload = {
        "order": {"symbol": "msft", "side": "sell", "type": "market", "qty": 2},
        "scheduled_at": _future_iso(),
    }
    client.post("/paper/scheduled-orders", json=payload)

    r = client.get("/paper/scheduled-orders")
    assert r.status_code == 200
    symbols = [row["symbol"] for row in r.json()]
    assert "MSFT" in symbols


def test_cancel_scheduled_order(client):
    payload = {
        "order": {"symbol": "nvda", "side": "buy", "type": "market", "qty": 1},
        "scheduled_at": _future_iso(),
    }
    created = client.post("/paper/scheduled-orders", json=payload).json()

    r = client.delete(f"/paper/scheduled-orders/{created['id']}")
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"


def test_cancel_already_cancelled_scheduled_order_conflicts(client):
    payload = {
        "order": {"symbol": "nvda", "side": "buy", "type": "market", "qty": 1},
        "scheduled_at": _future_iso(),
    }
    created = client.post("/paper/scheduled-orders", json=payload).json()
    client.delete(f"/paper/scheduled-orders/{created['id']}")

    r = client.delete(f"/paper/scheduled-orders/{created['id']}")
    assert r.status_code == 409


def test_cancel_unknown_scheduled_order_404s(client):
    r = client.delete("/paper/scheduled-orders/999999")
    assert r.status_code == 404
