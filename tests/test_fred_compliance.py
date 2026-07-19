"""FRED compliance guarantees (requirement #9).

Proves, with offline tests, that the FRED integration:
  - retrieves only allowlisted, public-domain series (blocks unknown + copyrighted);
  - never enters any database (Bronze/Silver/Gold) - the endpoint path persists nothing;
  - is isolated from persistence, AI/ML, scoring, backtesting, and order code
    (import-graph check, both directions);
  - never logs response bodies or observation values;
  - always carries the required FRED attribution notice and per-indicator attribution;
  - serves every response with Cache-Control: no-store;
  - is kill-switchable: disabled/unconfigured FRED leaves the app fully working;
  - keeps the API key out of the frontend (backend-only, by name).
"""
from __future__ import annotations

import ast
import datetime as dt
import logging
import pathlib

import pytest

from catalystiq.config import Settings, get_settings
from catalystiq.db import models
from catalystiq.fred import allowlist, service
from catalystiq.fred.allowlist import CopyrightStatus, SeriesBlocked, SeriesNotAllowed
from catalystiq.main import app
from catalystiq.schemas.macro import MacroObservation

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _enabled_settings() -> Settings:
    return Settings(enable_fred=True, fred_api_key="test-key", action_api_key="ci-test-key")


class _FakeFredClient:
    """Stand-in for FredClient that returns canned observations - no network."""

    SENTINEL = 987654.321

    def __init__(self, api_key, transport=None):
        assert api_key  # constructed only when configured
        self.api_key = api_key

    def get_observations(self, series_id, observation_start=None, observation_end=None, as_of=None):
        retrieved = dt.datetime.now(dt.timezone.utc)
        return [
            MacroObservation(
                series_id=series_id, observation_date=dt.date(2026, 6, 30),
                value=1.0, units="Percent", source="fred", retrieved_at=retrieved,
            ),
            MacroObservation(
                series_id=series_id, observation_date=dt.date(2026, 7, 1),
                value=self.SENTINEL, units="Percent", source="fred", retrieved_at=retrieved,
            ),
        ]


# --- Allowlist + copyright gate ----------------------------------------


def test_unknown_series_is_blocked():
    with pytest.raises(SeriesNotAllowed):
        allowlist.require_retrievable("TOTALLY_MADE_UP")


def test_copyrighted_series_are_hard_blocked():
    for blocked in ("VIXCLS", "SP500"):
        spec = allowlist.get_spec(blocked)
        assert spec is not None
        assert spec.copyright_status is CopyrightStatus.COPYRIGHTED_PREAPPROVAL
        assert spec.retrievable is False
        with pytest.raises(SeriesBlocked):
            allowlist.require_retrievable(blocked)
    approved_ids = {s.series_id for s in allowlist.approved_series()}
    assert "VIXCLS" not in approved_ids and "SP500" not in approved_ids


def test_approved_series_are_all_public_domain():
    for spec in allowlist.approved_series():
        assert spec.copyright_status is CopyrightStatus.PUBLIC_DOMAIN
        assert "via FRED" in spec.attribution


def test_service_single_series_gate(monkeypatch):
    monkeypatch.setattr(service, "FredClient", _FakeFredClient)
    with pytest.raises(SeriesNotAllowed):
        service.get_series_context(_enabled_settings(), "NOPE")
    with pytest.raises(SeriesBlocked):
        service.get_series_context(_enabled_settings(), "VIXCLS")


# --- Attribution + notice ----------------------------------------------


def test_context_carries_required_notice_and_attribution(monkeypatch):
    monkeypatch.setattr(service, "FredClient", _FakeFredClient)
    ctx = service.build_context(_enabled_settings())
    assert ctx["available"] is True
    assert ctx["notice"] == service.REQUIRED_NOTICE
    assert "not endorsed or certified by the Federal Reserve Bank of St. Louis" in ctx["notice"]
    assert ctx["indicators"], "expected allowlisted indicators"
    for ind in ctx["indicators"]:
        assert "via FRED" in ind["attribution"]
        assert ind["status"] == "ok"


# --- No persistence (ephemeral) ----------------------------------------


def test_context_endpoint_persists_nothing(client, test_db_session, monkeypatch):
    monkeypatch.setattr(service, "FredClient", _FakeFredClient)
    app.dependency_overrides[get_settings] = _enabled_settings
    try:
        resp = client.get("/fred/context")
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert resp.status_code == 200
    assert resp.json()["available"] is True
    # Nothing FRED touched may have landed in any persisted table.
    assert test_db_session.query(models.BronzeIngestionRun).count() == 0
    assert test_db_session.query(models.BronzeRawDocument).count() == 0
    assert test_db_session.query(models.SilverMacroObservation).count() == 0
    assert test_db_session.query(models.SilverMacroSeries).count() == 0


def test_build_context_takes_no_db_session():
    # The service signature must not accept a db/session argument at all.
    import inspect

    params = set(inspect.signature(service.build_context).parameters)
    assert "db" not in params and "session" not in params


# --- No-store header ---------------------------------------------------


def test_context_endpoint_sets_no_store(client):
    resp = client.get("/fred/context")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-store"


def test_series_list_endpoint_sets_no_store(client):
    resp = client.get("/fred/series")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-store"
    body = resp.json()
    assert body["notice"] == service.REQUIRED_NOTICE
    ids = {s["series_id"] for s in body["series"]}
    assert {"VIXCLS", "SP500"}.issubset(ids)  # documented as blocked


# --- Kill switch -------------------------------------------------------


def test_disabled_fred_is_a_no_op_not_an_error(client):
    # Default test settings have FRED off: the panel reports unavailable, 200.
    resp = client.get("/fred/context")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["notice"] == service.REQUIRED_NOTICE
    assert body["indicators"] == []
    # And the rest of the app is unaffected.
    assert client.get("/health").status_code == 200


def test_blocked_series_endpoint_returns_403(client):
    app.dependency_overrides[get_settings] = _enabled_settings
    try:
        assert client.get("/fred/series/VIXCLS").status_code == 403
        assert client.get("/fred/series/NOT_A_SERIES").status_code == 404
    finally:
        app.dependency_overrides.pop(get_settings, None)


# --- No logging of bodies / values -------------------------------------


def test_no_observation_value_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(service, "FredClient", _FakeFredClient)
    with caplog.at_level(logging.DEBUG):
        service.build_context(_enabled_settings())
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert str(_FakeFredClient.SENTINEL) not in blob
    assert "987654" not in blob


# --- Isolation (import graph, both directions) -------------------------

_FORBIDDEN_IMPORTS = (
    "catalystiq.db",
    "catalystiq.pipelines",
    "catalystiq.analysis",
    "catalystiq.orders",
    "catalystiq.scheduler",
    "catalystiq.validation",
)


def _imported_modules(path: pathlib.Path) -> set[str]:
    """Every module name imported anywhere in a file (module- or function-level),
    via `import x` or `from x import ...`. Parses the AST so docstrings and
    comments that merely mention a module name are ignored."""
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_fred_package_does_not_import_persistence_or_ml():
    fred_dir = _REPO_ROOT / "catalystiq" / "fred"
    for path in fred_dir.glob("*.py"):
        for module in _imported_modules(path):
            for forbidden in _FORBIDDEN_IMPORTS:
                assert not module.startswith(forbidden), (
                    f"{path.name} must not import {module}"
                )


def test_scoring_and_order_modules_do_not_import_fred():
    # The reverse guard: nothing in the ML/scoring/execution surface may pull
    # FRED in, so FRED can never feed a score, backtest, or order.
    targets = list((_REPO_ROOT / "catalystiq" / "analysis").rglob("*.py"))
    targets += list((_REPO_ROOT / "catalystiq" / "validation").rglob("*.py"))
    targets += [_REPO_ROOT / "catalystiq" / "orders.py"]
    targets += [_REPO_ROOT / "catalystiq" / "scheduler.py"]
    for path in targets:
        for module in _imported_modules(path):
            assert not module.startswith("catalystiq.fred"), (
                f"{path} must not import catalystiq.fred"
            )


# --- Secret handling: key is backend-only ------------------------------


def test_frontend_never_references_the_fred_key():
    frontend_src = _REPO_ROOT / "frontend" / "src"
    for path in frontend_src.rglob("*.ts*"):
        text = path.read_text().lower()
        assert "fred_api_key" not in text, f"{path} must not reference the FRED key"
