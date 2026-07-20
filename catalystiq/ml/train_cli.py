"""Operator CLI for the historical model-validation + MLflow experiment.

Runs the real offline training/evaluation phase against validated Silver data
in an environment that has market-data access, recording everything to MLflow:

    python -m catalystiq.ml.train_cli \
        --symbols AAPL,MSFT,NVDA,JPM,XOM,SPY,QQQ \
        --benchmark SPY \
        --start 2015-01-01 \
        --end 2026-06-30 \
        --horizons 1,5,10,20 \
        --ingest \
        --enable-training

Smoke test first (small universe + short range) to validate the wiring before
the full experiment. This is deliberately more limited than genuine
validation - do NOT treat a five-symbol, one-year smoke run as proof a model
works; real validation needs a broad universe, multiple market regimes, a long
history and ideally delisted names:

    python -m catalystiq.ml.train_cli \
        --symbols AAPL,MSFT,SPY \
        --benchmark SPY \
        --start 2020-01-01 \
        --end 2021-06-30 \
        --horizons 5 \
        --ingest \
        --enable-training \
        --smoke-test

MLflow is configured entirely through the environment - no URL or credential is
hard-coded. Leave ``MLFLOW_TRACKING_URI`` unset to record to a local ``mlruns``
directory; point it at a server to use a shared backend. Then browse the
results with:

    mlflow ui --port 5000        # then open http://127.0.0.1:5000

This runner FAILS CLOSED: it only runs when ``--enable-training`` is passed,
which constructs a local settings object with ``ENABLE_ML`` + ``ENABLE_ML_TRAINING``
on FOR THIS PROCESS ONLY. It never changes persisted config and never enables
inference, serving, frontend predictions, model approval, registry promotion or
order submission. Any artifacts registered (only with ``--register-candidates``)
are ``candidate`` and never approved; synthetic datasets are flagged and can
never be promoted.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from catalystiq.config import Settings
from catalystiq.ml.experiment import run_experiment


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="catalystiq.ml.train_cli",
        description="Historical model-validation + MLflow experiment (offline, fail-closed).",
    )
    p.add_argument("--symbols", required=True, help="Comma-separated ticker universe.")
    p.add_argument("--benchmark", default="SPY", help="Benchmark symbol (default SPY).")
    p.add_argument("--start", required=True, help="First prediction date, YYYY-MM-DD.")
    p.add_argument("--end", required=True, help="Last prediction date, YYYY-MM-DD.")
    p.add_argument("--horizons", default="5", help="Comma-separated label horizons in trading days.")
    p.add_argument("--step-days", type=int, default=7, help="Spacing between prediction dates (default weekly).")
    p.add_argument("--direction", default="long", choices=["long", "short"])
    p.add_argument("--database-url", default=None, help="SQLAlchemy URL (default: app settings).")
    p.add_argument("--ingest", action="store_true", help="Ingest/refresh Silver for each symbol first.")
    p.add_argument("--enable-training", action="store_true",
                   help="Enable ML + ML training for THIS offline run (required; fail-closed without it).")
    p.add_argument("--register-candidates", action="store_true",
                   help="Register fitted models as CANDIDATE artifacts (never approved).")
    p.add_argument("--experiment-name", default=None,
                   help="Override MLFLOW_EXPERIMENT_NAME for this run.")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--output-dir", default="ml_runner_output",
                   help="Local artifact dir used when MLflow is not installed.")
    p.add_argument("--smoke-test", action="store_true",
                   help="Documentation flag: marks this as a smoke run (also sets a smoke tag).")
    return p


def _offline_settings(enable: bool, database_url: str | None, experiment_name: str | None) -> Settings:
    """Settings for THIS process only. Enabling flags here does not touch any
    persisted config and never enables inference/serving/approval."""
    overrides: dict = {}
    if enable:
        overrides.update(enable_ml=True, enable_ml_training=True)
    if database_url:
        overrides.update(database_url=database_url)
    if experiment_name:
        overrides.update(mlflow_experiment_name=experiment_name)
    overrides.setdefault("action_api_key", "offline-training-run")
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
    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]
    settings = _offline_settings(args.enable_training, args.database_url, args.experiment_name)

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

        report = run_experiment(
            db,
            symbols=symbols,
            benchmark=args.benchmark,
            start=dt.date.fromisoformat(args.start),
            end=dt.date.fromisoformat(args.end),
            step_days=args.step_days,
            horizons=horizons,
            direction=args.direction,
            settings=settings,
            register=args.register_candidates,
            seed=args.seed,
            output_dir=args.output_dir,
        )
    finally:
        if owns_db:
            db.close()

    payload = report.to_dict()
    payload["ingest_warnings"] = ingest_warnings
    payload["smoke_test"] = args.smoke_test
    print(json.dumps(payload, indent=2, default=str))

    # Exit non-zero if no horizon passed its gate (nothing was trainable yet).
    any_trained = any(
        any(m.get("trained") for m in hz.get("models", []))
        for hz in payload.get("horizons_results", [])
    )
    return 0 if any_trained else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
