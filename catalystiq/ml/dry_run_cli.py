"""Operator CLI for the chronological training dry-run.

Runs the offline dry-run against real validated data in an environment that has
market-data access:

    python -m catalystiq.ml.dry_run_cli \
        --symbols AAPL,MSFT,NVDA,JPM,XOM --benchmark SPY \
        --start 2020-01-01 --end 2021-06-30 --horizon 5 --enable --ingest

It is a deliberate, offline diagnostic. It **fails closed**: the dry-run only
runs when ``--enable`` is passed (which constructs a local settings object with
``ENABLE_ML`` + ``ENABLE_ML_TRAINING`` on for this process only - it does NOT
change any persisted config and never enables inference, serving, or approval).
With ``--ingest`` it first brings each symbol's Silver up to date through the
app's own pipeline; a per-symbol fetch failure (e.g. a network-policy denial or
rate limit) is reported and skipped, never faked. It prints the ``DryRunReport``
as JSON and exits non-zero if the data is not yet sufficient for training.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from catalystiq.config import Settings
from catalystiq.ml.dry_run import run_training_dry_run


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="catalystiq.ml.dry_run_cli",
                                description="Chronological training dry-run (offline, fail-closed).")
    p.add_argument("--symbols", required=True, help="Comma-separated ticker universe.")
    p.add_argument("--benchmark", default="SPY", help="Benchmark symbol (default SPY).")
    p.add_argument("--start", required=True, help="First prediction date, YYYY-MM-DD.")
    p.add_argument("--end", required=True, help="Last prediction date, YYYY-MM-DD.")
    p.add_argument("--step-days", type=int, default=7, help="Spacing between prediction dates (default weekly).")
    p.add_argument("--horizon", type=int, default=5, help="Label horizon in trading days.")
    p.add_argument("--direction", default="long", choices=["long", "short"])
    p.add_argument("--database-url", default=None, help="SQLAlchemy URL (default: app settings).")
    p.add_argument("--ingest", action="store_true", help="Ingest/refresh Silver for each symbol first.")
    p.add_argument("--enable", action="store_true",
                   help="Enable ML + ML training for THIS offline run (required; fail-closed without it).")
    p.add_argument("--min-examples-to-fit", type=int, default=60)
    return p


def _prediction_dates(start: str, end: str, step_days: int) -> list[dt.datetime]:
    d0 = dt.date.fromisoformat(start)
    d1 = dt.date.fromisoformat(end)
    if d1 < d0:
        raise ValueError("--end must be on or after --start")
    out: list[dt.datetime] = []
    d = d0
    step = max(1, step_days)
    while d <= d1:
        out.append(dt.datetime(d.year, d.month, d.day, 20, 0, 0))  # session close (UTC)
        d += dt.timedelta(days=step)
    return out


def _offline_settings(enable: bool, database_url: str | None) -> Settings:
    """Settings for THIS process only. Enabling flags here does not touch any
    persisted config and never enables inference/serving/approval."""
    overrides: dict = {}
    if enable:
        overrides.update(enable_ml=True, enable_ml_training=True)
    if database_url:
        overrides.update(database_url=database_url)
    if not overrides.get("action_api_key"):
        overrides.setdefault("action_api_key", "offline-dry-run")
    return Settings(**overrides)


def _ingest(symbols: list[str], benchmark: str, db) -> list[str]:
    """Best-effort Silver refresh via the app's own pipeline. Returns warnings
    for any symbol that could not be fetched (never fabricated)."""
    from catalystiq.pipelines.market_price_pipeline import ensure_fresh
    from catalystiq.providers.market_data import MarketDataError, get_market_data_provider

    warnings: list[str] = []
    provider = get_market_data_provider()
    for sym in [*symbols, benchmark]:
        try:
            ensure_fresh(sym, provider, db)
        except MarketDataError as exc:
            warnings.append(f"ingest failed for {sym}: {exc}")
    return warnings


def main(argv: list[str] | None = None, *, db=None) -> int:
    args = build_parser().parse_args(argv)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    settings = _offline_settings(args.enable, args.database_url)

    owns_db = db is None
    if owns_db:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from catalystiq.db.base import Base
        from catalystiq.db import models  # noqa: F401 - register tables

        engine = create_engine(settings.database_url)
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()

    ingest_warnings: list[str] = []
    try:
        if args.ingest:
            ingest_warnings = _ingest(symbols, args.benchmark, db)

        report = run_training_dry_run(
            db,
            symbols=symbols,
            prediction_timestamps=_prediction_dates(args.start, args.end, args.step_days),
            direction=args.direction,
            horizon_days=args.horizon,
            benchmark=args.benchmark,
            settings=settings,
            min_examples_to_fit=args.min_examples_to_fit,
        )
    finally:
        if owns_db:
            db.close()

    payload = report.to_dict()
    payload["ingest_warnings"] = ingest_warnings
    print(json.dumps(payload, indent=2, default=str))
    return 0 if report.sufficiency.get("sufficient_for_training") else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
