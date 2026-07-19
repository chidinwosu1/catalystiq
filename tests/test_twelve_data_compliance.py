"""Twelve Data restricted personal-use compliance guarantees.

Proves, offline:
  - central credit limits (per-minute + per-day) are enforced, weighted;
  - the provider auto-shuts-off on the daily cap and on credential/licensing
    failure, and the daily cap clears on UTC day rollover;
  - raw Twelve Data values (and reconstructable diffs) are never persisted;
  - TD is isolated from analysis/scoring/backtesting/order code;
  - the API key never appears in the frontend and is redacted from logs;
  - TD stays optional (disabled -> the app keeps working).
"""
from __future__ import annotations

import ast
import datetime as dt
import pathlib

import pytest

from catalystiq.pipelines import comparison as cmp
from catalystiq.providers.base import ProviderError, ProviderErrorCategory
from catalystiq.providers.market_data import MarketDataError, MarketDataProvider
from catalystiq.providers.transport import HttpResponse, redact
from catalystiq.providers.twelve_data import TwelveDataProvider
from catalystiq.providers.twelve_data_gate import TwelveDataGate, reset_twelve_data_gate
from catalystiq.schemas.market_data import OHLCVBar, Quote

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _fresh_singleton_gate():
    reset_twelve_data_gate()
    yield
    reset_twelve_data_gate()


class _Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


class _FixedTransport:
    """Returns one scripted HttpResponse for every request."""

    def __init__(self, status, text):
        self._status, self._text = status, text
        self.calls = 0

    def request(self, method, url, *, params=None, headers=None, json=None):
        self.calls += 1
        return HttpResponse(self._status, {}, self._text, url, 1.0, 0, "twelve_data")


_QUOTE = '{"symbol":"AAPL","close":"195.20","previous_close":"194.00","timestamp":1752710400}'


# --- Central credit limits (req. #3, #4) -------------------------------


def test_per_minute_credit_limit_and_reset():
    clock = _Clock()
    gate = TwelveDataGate(credits_per_minute=2, credits_per_day=1000, monotonic=clock)
    gate.charge(1)
    gate.charge(1)
    with pytest.raises(ProviderError) as exc:
        gate.charge(1)
    assert exc.value.category is ProviderErrorCategory.RATE_LIMITED
    # After the 60s window rolls, capacity is available again.
    clock.t += 61
    gate.charge(1)


def test_endpoint_credit_weights_are_tracked():
    gate = TwelveDataGate(credits_per_minute=10, credits_per_day=10)
    gate.charge(3)  # a heavier endpoint costs >1 credit
    assert gate.status()["credits_used_this_minute"] == 3
    assert gate.status()["credits_used_today"] == 3


def test_daily_cap_disables_then_clears_on_day_rollover():
    day = {"d": dt.date(2026, 7, 19)}
    gate = TwelveDataGate(
        credits_per_minute=1000, credits_per_day=3, utc_date=lambda: day["d"]
    )
    gate.charge(1)
    gate.charge(1)
    gate.charge(1)
    with pytest.raises(ProviderError) as exc:
        gate.charge(1)  # exceeds daily cap
    assert exc.value.category is ProviderErrorCategory.RATE_LIMITED
    assert gate.disabled is True  # auto shut off
    # New UTC day clears the daily-cap auto-disable and resets the counter.
    day["d"] = dt.date(2026, 7, 20)
    assert gate.disabled is False
    gate.charge(1)


# --- Auto-shutoff on credential / licensing failure (req. #14) ---------


def _provider(status, text, gate=None):
    return TwelveDataProvider(
        "k", transport=_FixedTransport(status, text), gate=gate or TwelveDataGate()
    )


def test_auth_failure_auto_disables_provider():
    gate = TwelveDataGate()
    provider = _provider(401, "{}", gate=gate)
    with pytest.raises(ProviderError) as exc:
        provider.get_quote("AAPL")
    assert exc.value.category is ProviderErrorCategory.AUTH
    assert gate.disabled is True
    # Once disabled, further calls fail closed (UNAVAILABLE) without a request.
    with pytest.raises(ProviderError) as exc2:
        provider.get_quote("AAPL")
    assert exc2.value.category is ProviderErrorCategory.UNAVAILABLE


def test_licensing_error_body_auto_disables():
    gate = TwelveDataGate()
    body = '{"status":"error","message":"This data requires a professional plan; upgrade your plan","code":403}'
    provider = _provider(200, body, gate=gate)
    with pytest.raises(ProviderError):
        provider.get_quote("AAPL")
    assert gate.disabled is True


def test_invalid_key_body_auto_disables():
    gate = TwelveDataGate()
    body = '{"status":"error","message":"Invalid API key provided","code":401}'
    provider = _provider(200, body, gate=gate)
    with pytest.raises(ProviderError):
        provider.get_quote("AAPL")
    assert gate.disabled is True


def test_benign_error_body_does_not_disable():
    # A plain "symbol not found" must NOT latch the provider off.
    gate = TwelveDataGate()
    body = '{"status":"error","message":"symbol not found","code":404}'
    provider = _provider(200, body, gate=gate)
    with pytest.raises(ProviderError):
        provider.get_quote("NOPE")
    assert gate.disabled is False


def test_provider_is_marked_restricted():
    assert TwelveDataProvider.RESTRICTED_NO_RAW_PERSIST is True


# --- No raw persistence / no reconstruction (req. #8, #11, #12) --------


class _StubProvider(MarketDataProvider):
    def __init__(self, name, price):
        self.PROVIDER_NAME = name
        self._price = price

    def get_quote(self, symbol):
        return Quote(symbol=symbol, price=self._price, as_of=dt.datetime.now(dt.timezone.utc))

    def get_ohlcv(self, symbol, start, end=None, interval="1d"):
        return [OHLCVBar(date=start, open=1, high=1, low=1, close=self._price, volume=1)]

    def get_fundamentals(self, symbol):
        raise MarketDataError("n/a")

    def get_news(self, symbol, limit=10):
        raise MarketDataError("n/a")


class _RestrictedStub(_StubProvider):
    RESTRICTED_NO_RAW_PERSIST = True


def test_restricted_secondary_persists_no_raw_value(test_db_session):
    db = test_db_session
    td_price = 106.0  # clearly beyond the 0.5% tolerance
    row = cmp.compare_quotes(
        "AAPL", db, _StubProvider("yahoo", 100.0), _RestrictedStub("twelve_data", td_price),
        tolerance_pct=0.5,
    )
    # Provenance kept; tolerance outcome kept; raw value + reconstructable diffs NOT.
    assert row.secondary_provider == "twelve_data"
    assert row.within_tolerance is False
    # The sanitized reason must not carry the numeric difference.
    assert "%" not in (row.selected_reason or "")
    assert row.secondary_value is None
    assert row.secondary_timestamp is None
    assert row.absolute_diff is None
    assert row.relative_diff_pct is None
    # Nothing stored lets the TD price be reconstructed.
    stored = [row.primary_value, row.secondary_value, row.absolute_diff, row.relative_diff_pct]
    assert td_price not in stored
    assert str(td_price) not in (row.selected_reason or "")


# --- Secret handling (req. #2, #7) -------------------------------------


def test_api_key_is_redacted_in_logs():
    assert redact({"apikey": "super-secret"})["apikey"] == "***"


def test_frontend_never_references_the_twelve_data_key():
    frontend_src = _REPO_ROOT / "frontend" / "src"
    for path in frontend_src.rglob("*.ts*"):
        assert "twelve_data_api_key" not in path.read_text().lower(), path


# --- Isolation from analysis/scoring/backtesting/orders (req. #9) ------


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_analysis_and_order_modules_do_not_import_twelve_data():
    targets = list((_REPO_ROOT / "catalystiq" / "analysis").rglob("*.py"))
    targets += [_REPO_ROOT / "catalystiq" / "orders.py"]
    targets += [_REPO_ROOT / "catalystiq" / "scheduler.py"]
    for path in targets:
        for module in _imported_modules(path):
            assert "twelve_data" not in module, f"{path} must not import {module}"


# --- Optional / kill switch (req. #15) ---------------------------------


def test_disabled_twelve_data_is_optional(client):
    # Default test settings have TD off: the compare endpoint refuses cleanly...
    resp = client.post("/data-quality/market_data/compare/AAPL")
    assert resp.status_code == 400
    # ...and the rest of the app is unaffected.
    assert client.get("/health").status_code == 200
