"""In-process record of when each data source was last fetched on demand.

On-demand sources (live quotes, fundamentals, news) are fetched per request and
never persisted as ingestion runs, so the health report has no Bronze "last
ingest" timestamp for them - which makes a continuously-updated source look
stale (a blank "—") when it is actually the freshest data in the app. This
lightweight tracker records the last successful *fetch* per source so the Data
Sources page can show "last fetched" instead of nothing.

Scope, by design: per-process and non-persistent. It reflects this API
process's own live activity, resets on restart, and is not shared across
workers. That's the right fidelity for an "is this source actively serving
fresh data right now" signal; durable freshness for persisted domains still
comes from BronzeIngestionRun.
"""
from __future__ import annotations

import datetime as dt
import threading

_LOCK = threading.Lock()
_LAST_FETCH: dict[str, dt.datetime] = {}


def record_fetch(source: str, when: dt.datetime | None = None) -> None:
    """Record that `source` served a successful on-demand fetch just now."""
    ts = when or dt.datetime.now(dt.timezone.utc)
    with _LOCK:
        _LAST_FETCH[source] = ts


def get_last_fetch(source: str) -> dt.datetime | None:
    """The most recent successful fetch time for `source`, or None."""
    with _LOCK:
        return _LAST_FETCH.get(source)


def reset() -> None:
    """Test helper: clear all recorded fetches."""
    with _LOCK:
        _LAST_FETCH.clear()
