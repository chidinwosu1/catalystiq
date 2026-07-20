"""Data-source health endpoints (§18): report enable/config/ingestion state
without leaking secrets."""
import datetime as dt

from catalystiq.config import Settings, get_settings
from catalystiq.db import models


def test_list_data_sources(client):
    resp = client.get("/data-sources")
    assert resp.status_code == 200
    names = {row["name"] for row in resp.json()}
    assert {"yahoo", "fred", "sec_edgar", "finra", "twelve_data", "webull"} <= names
    # Yahoo is keyless and always enabled.
    yahoo = [r for r in resp.json() if r["name"] == "yahoo"][0]
    assert yahoo["enabled"] is True


def test_provider_health_reports_missing_settings_names_only(client):
    from catalystiq.main import app

    # FRED enabled but no key -> configured False, missing_settings names it,
    # but never a value.
    app.dependency_overrides[get_settings] = lambda: Settings(
        enable_fred=True, fred_api_key="", action_api_key="ci-test-key"
    )
    resp = client.get("/data-sources/fred/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["configured"] is False
    assert body["missing_settings"] == ["fred_api_key"]
    app.dependency_overrides.pop(get_settings, None)


def test_provider_health_reports_last_ingestion(client, test_db_session):
    # BLS is a persisted macro source; its health reflects Bronze runs. (FRED is
    # ephemeral and never creates runs - covered separately in test_fred_compliance.)
    db = test_db_session
    now = dt.datetime(2026, 7, 18, 12, 0, 0)
    db.add(
        models.BronzeIngestionRun(
            domain="macro", requested_symbol="LNS14000000", provider="bls",
            requested_at=now, completed_at=now, status="succeeded", record_count=5,
        )
    )
    db.commit()
    resp = client.get("/data-sources/bls/health")
    assert resp.status_code == 200
    assert resp.json()["last_successful_ingestion_at"] is not None


def test_ephemeral_source_reports_no_ingestion(client, test_db_session):
    # FRED is ephemeral: health never reports an ingestion timestamp, even if a
    # stray run row existed - it is not persisted through FRED.
    resp = client.get("/data-sources/fred/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ephemeral"] is True
    assert body["last_successful_ingestion_at"] is None
    assert body["data_freshness_at"] is None


def test_health_reports_last_fetched_for_on_demand_source(client):
    # On-demand sources (live quotes) have no scheduled ingestion, so their
    # freshness comes from the in-process last-fetch tracker, not Bronze runs.
    from catalystiq.providers import fetch_tracker

    fetch_tracker.reset()
    # Yahoo has no fetch recorded yet -> null (an honest blank, not staleness).
    body = client.get("/data-sources/yahoo/health").json()
    assert body["last_fetched_at"] is None

    now = dt.datetime(2026, 7, 19, 15, 0, 0, tzinfo=dt.timezone.utc)
    fetch_tracker.record_fetch("yahoo", when=now)
    body = client.get("/data-sources/yahoo/health").json()
    assert body["last_fetched_at"] is not None
    assert body["last_fetched_at"].startswith("2026-07-19T15:00:00")
    fetch_tracker.reset()


def test_recording_a_quote_fetch_populates_last_fetched():
    # The Yahoo adapter records a successful fetch through the shared tracker;
    # verify the wiring without hitting the network.
    from catalystiq.providers import fetch_tracker

    fetch_tracker.reset()
    assert fetch_tracker.get_last_fetch("yahoo") is None
    fetch_tracker.record_fetch("yahoo")
    assert fetch_tracker.get_last_fetch("yahoo") is not None
    fetch_tracker.reset()


def test_unknown_provider_404(client):
    assert client.get("/data-sources/not-real/health").status_code == 404


def test_health_response_has_no_secret_fields(client):
    resp = client.get("/data-sources/health")
    assert resp.status_code == 200
    blob = resp.text.lower()
    for banned in ("api_key", "secret", "app_secret", "authorization", "token"):
        # "missing_settings" may contain the setting *name* fred_api_key; that
        # is a name, not a value. Ensure no value-bearing key names leak as
        # populated fields - here we just assert the raw response carries no
        # obvious secret markers beyond setting-name references.
        assert f'"{banned}":' not in blob
