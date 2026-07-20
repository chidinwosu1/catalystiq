"""Staged long-history validation (offline, fail-closed dry-run).

A deliberate validation STAGE to run before scaling to a large universe. For a
small symbol set over a long range it:

  1. (optionally) ingests each symbol's Silver, fetching enough history to
     cover ``--start`` minus an indicator warm-up;
  2. AUDITS per-symbol coverage against the exchange calendar and FAILS CLOSED
     (status ``incomplete_history``, non-zero exit) if any symbol's history is
     incomplete - before doing any expensive model work;
  3. builds the point-in-time feature vector ONCE per (symbol, date) and reuses
     it across all requested horizons (labels differ per horizon, features do
     not), then runs the chronological dry-run diagnostics per horizon;
  4. reports, per symbol: requested range, earliest/latest ingested bar, raw
     bar count, missing sessions, ingestion gaps, usable vs skipped examples,
     and feature coverage by period.

It is a DRY RUN: no model is registered, approved, deployed, or served. Model
fitting requires ``--enable`` (ENABLE_ML + ENABLE_ML_TRAINING for this process
only) and scikit-learn; otherwise diagnostics still run and fitting is skipped.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time


def _log(msg: str) -> None:
    """Progress goes to STDERR so the report JSON on stdout stays clean for
    redirection to a file."""
    print(f"[validate_history] {msg}", file=sys.stderr, flush=True)

from catalystiq.config import Settings
from catalystiq.ml import flags
from catalystiq.ml.dataset.builder import TrainingDataset, TrainingExample, atr_barrier_planner
from catalystiq.ml.dry_run import run_training_dry_run
from catalystiq.ml.features.pit_provider import SilverPointInTimeProvider
from catalystiq.ml.features.schema import (
    FEATURE_CATALOG,
    build_feature_vector,
    missing_indicator_name,
)
from catalystiq.ml.history_audit import (
    audit_symbol_coverage,
    feature_coverage_by_period,
)
from catalystiq.ml.labels.outcomes import generate_outcome_labels

INDICATOR_WARMUP_DAYS = 420


def _prediction_dates(start: dt.date, end: dt.date, step_days: int) -> list[dt.datetime]:
    out: list[dt.datetime] = []
    d = start
    step = max(1, step_days)
    while d <= end:
        out.append(dt.datetime(d.year, d.month, d.day, 22, 0, 0))  # after typical close
        d += dt.timedelta(days=step)
    return out


def _spread_fraction(vector: dict) -> float | None:
    bps = vector.get("estimated_spread_bps")
    try:
        return float(bps) / 10_000.0 if bps is not None else None
    except (TypeError, ValueError):
        return None


def _ingest(symbols, benchmark, db, *, start: dt.date) -> list[str]:
    from catalystiq.pipelines.market_price_pipeline import ensure_fresh
    from catalystiq.providers.market_data import MarketDataError, get_market_data_provider

    days = max((dt.date.today() - start).days + INDICATOR_WARMUP_DAYS, 365 * 2)
    warnings: list[str] = []
    provider = get_market_data_provider()
    for sym in [*symbols, benchmark]:
        try:
            ensure_fresh(sym, provider, db, days=days)
        except MarketDataError as exc:
            warnings.append(f"ingest failed for {sym}: {exc}")
    return warnings


def _build_multi_horizon(provider, symbols, dates, direction, horizons, *, is_synthetic, progress=False):
    """Build one TrainingDataset per horizon, computing each (symbol, date)
    feature vector ONCE and reusing it across horizons. Returns
    (datasets, per_symbol_counts, dated_vectors)."""
    max_h = max(horizons)
    datasets = {h: TrainingDataset(is_synthetic=is_synthetic,
                                   source_providers=["computed", "sec_edgar", "bls", "bea"])
                for h in horizons}
    per_symbol = {s.upper(): {"usable": 0, "skipped": 0} for s in symbols}
    dated_vectors: list[tuple[dt.datetime, dict]] = []

    total = len(symbols) * len(dates)
    done = 0
    start_t = time.monotonic()
    tick = max(1, total // 40)  # ~2.5% increments
    if progress:
        _log(f"building point-in-time features for {total} (symbol, date) pairs "
             f"[{len(symbols)} symbols x {len(dates)} dates], reused across horizons {horizons}...")

    for sym in symbols:
        sym = sym.upper()
        for ts in dates:
            done += 1
            if progress and (done % tick == 0 or done == total):
                elapsed = time.monotonic() - start_t
                rate = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / rate if rate > 0 else 0
                _log(f"features {done}/{total} ({done*100//total}%) | "
                     f"elapsed {elapsed/60:.1f} min | ETA ~{eta/60:.1f} min | latest {sym}@{ts.date()}")
            raw = provider.get_features(sym, ts)
            vector, rejections = build_feature_vector(raw, for_training=True, strict=True)
            present = sum(1 for n in FEATURE_CATALOG if vector.get(missing_indicator_name(n)) == 0)
            if present == 0:
                _skip_all(datasets, sym, ts, "no point-in-time feature coverage as-of timestamp")
                per_symbol[sym]["skipped"] += 1
                continue
            entry = provider.get_executable_entry(sym, ts)
            if entry is None:
                _skip_all(datasets, sym, ts, "no contiguous next-session executable entry")
                per_symbol[sym]["skipped"] += 1
                continue
            entry_session, entry_price = entry
            path = provider.get_forward_path(sym, entry_session, max_h)
            if len(path) < max_h:
                # Not enough forward sessions to label the LONGEST horizon -
                # skip so every horizon's example set is comparable and no
                # short-horizon example borrows a truncated path.
                _skip_all(datasets, sym, ts, f"insufficient forward path ({len(path)}/{max_h} sessions)")
                per_symbol[sym]["skipped"] += 1
                continue

            per_symbol[sym]["usable"] += 1
            dated_vectors.append((ts, vector))
            gaps = [f.feature_name for f in raw if f.data_quality_status.value == "missing"]
            plan = atr_barrier_planner(entry_price, direction, vector)
            for h in horizons:
                labels = generate_outcome_labels(
                    symbol=sym, direction=direction, horizon_days=h,
                    executable_entry_price=entry_price,
                    target_price=plan.target_price, stop_price=plan.stop_price,
                    path=path[:h], estimated_spread_fraction=_spread_fraction(vector),
                    avg_daily_dollar_volume=vector.get("adv_dollar_20d"),
                )
                datasets[h].examples.append(TrainingExample(
                    symbol=sym, prediction_timestamp=ts, entry_session=entry_session,
                    direction=direction, horizon_days=h, features=vector, labels=labels,
                    feature_rejections=rejections, requirement_gaps=gaps,
                ))
    return datasets, per_symbol, dated_vectors


def _skip_all(datasets, sym, ts, reason):
    for ds in datasets.values():
        ds.skipped.append({"symbol": sym, "ts": ts.isoformat(), "reason": reason})


def run_history_validation(
    db,
    *,
    symbols: list[str],
    benchmark: str,
    start: dt.date,
    end: dt.date,
    horizons: list[int],
    step_days: int = 7,
    direction: str = "long",
    settings: Settings | None = None,
    require_complete_history: bool = True,
    is_synthetic_data: bool = False,
    max_missing_ratio: float = 0.02,
    max_gap_sessions: int = 5,
    audit_only: bool = False,
    progress: bool = False,
) -> dict:
    """Audit coverage, fail closed if incomplete, else run per-horizon dry-run
    diagnostics. Returns a JSON-able report dict.

    In ``audit_only`` mode this performs ONLY the coverage audit and returns
    immediately - it never builds features or fits a model (even when coverage
    passes) - so it does not require training to be enabled.
    """
    # The coverage audit is read-only (DB + calendar); only the model phase is
    # training-gated. In audit-only mode we never reach that phase.
    if not audit_only:
        flags.training_enabled(settings).require()  # fail closed unless enabled

    from catalystiq.pipelines.market_price_pipeline import get_silver_bars

    # 1) Coverage audit (fast; DB + calendar only).
    if progress:
        _log(f"auditing Silver coverage for {len(symbols)+1} symbols "
             f"({start.isoformat()} -> {end.isoformat()})...")
    coverage = {}
    for sym in [*symbols, benchmark]:
        bar_dates = [b.date for b in get_silver_bars(sym, db)]
        cov = audit_symbol_coverage(
            bar_dates, symbol=sym, start=start, end=end,
            max_missing_ratio=max_missing_ratio, max_gap_sessions=max_gap_sessions,
        )
        coverage[sym.upper()] = cov.to_dict()

    incomplete = [s for s, c in coverage.items() if not c["complete"]]
    report: dict = {
        "status": "ok",
        "symbols": [s.upper() for s in symbols],
        "benchmark": benchmark.upper(),
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "horizons": horizons,
        "step_days": step_days,
        "symbol_coverage": coverage,
    }

    # Audit-only: return the full per-symbol coverage report and STOP here -
    # before any feature building or model training, even if coverage passes.
    if progress:
        _log(f"coverage audit complete: {len(coverage) - len(incomplete)}/{len(coverage)} symbols complete"
             + (f"; incomplete: {', '.join(incomplete)}" if incomplete else ""))
    if audit_only:
        report["mode"] = "audit_only"
        report["all_symbols_complete"] = not incomplete
        report["incomplete_symbols"] = incomplete
        report["status"] = "audit_only"
        return report

    if require_complete_history and incomplete:
        report["status"] = "incomplete_history"
        report["incomplete_symbols"] = incomplete
        report["message"] = (
            "Requested history is incomplete for: " + ", ".join(incomplete)
            + ". Failing closed - fix ingestion (fresh DB / wider range) before validating."
        )
        return report

    # 2) Build features ONCE across horizons, then per-horizon diagnostics.
    provider = SilverPointInTimeProvider(db, benchmark_symbol=benchmark, sector_resolver=lambda s: None)
    dates = _prediction_dates(start, end, step_days)
    datasets, per_symbol, dated_vectors = _build_multi_horizon(
        provider, symbols, dates, direction, horizons, is_synthetic=is_synthetic_data,
        progress=progress,
    )

    report["per_symbol_examples"] = per_symbol
    report["requested_prediction_dates"] = len(dates)
    report["feature_coverage_by_period"] = feature_coverage_by_period(dated_vectors)

    horizon_reports = {}
    for h in horizons:
        if progress:
            _log(f"running chronological dry-run diagnostics for horizon {h}...")
        rep = run_training_dry_run(
            db, dataset=datasets[h], horizon_days=h, direction=direction,
            settings=settings, min_examples_to_fit=60,
        )
        d = rep.to_dict()
        # Trim the biggest nested blobs for a readable top-level report.
        d.pop("coverage", None)
        horizon_reports[str(h)] = {
            "dataset_size": rep.dataset_size,
            "date_coverage": rep.date_coverage,
            "folds": d["folds"],
            "labels": d["labels"],
            "sufficiency": rep.sufficiency,
            "models_trained": rep.models.trained,
        }
    report["horizons_results"] = horizon_reports
    report["overall_sufficient"] = all(
        hr["sufficiency"].get("sufficient_for_training") for hr in horizon_reports.values()
    )
    return report


# --- CLI --------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="catalystiq.ml.validate_history",
        description="Staged long-history validation (offline, fail-closed dry-run).",
    )
    p.add_argument("--symbols", required=True)
    p.add_argument("--benchmark", default="SPY")
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD")
    p.add_argument("--horizons", default="1,5,10,20")
    p.add_argument("--step-days", type=int, default=7)
    p.add_argument("--direction", default="long", choices=["long", "short"])
    p.add_argument("--database-url", default=None)
    p.add_argument("--ingest", action="store_true")
    p.add_argument("--audit-only", action="store_true",
                   help="Ingest (if --ingest) and print the per-symbol coverage report, then EXIT "
                        "before any feature building or model training. Read-only; --enable not needed.")
    p.add_argument("--enable", action="store_true",
                   help="Enable ML + training for THIS process only (required for the model phase; "
                        "not needed for --audit-only).")
    p.add_argument("--allow-incomplete-history", action="store_true",
                   help="Do NOT fail closed on incomplete coverage (diagnostic only; not recommended).")
    p.add_argument("--max-missing-ratio", type=float, default=0.02)
    p.add_argument("--max-gap-sessions", type=int, default=5)
    return p


def _offline_settings(enable: bool, database_url: str | None) -> Settings:
    overrides: dict = {"action_api_key": "offline-history-validation"}
    if enable:
        overrides.update(enable_ml=True, enable_ml_training=True)
    if database_url:
        overrides.update(database_url=database_url)
    return Settings(**overrides)


def main(argv: list[str] | None = None, *, db=None) -> int:
    args = build_parser().parse_args(argv)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]
    settings = _offline_settings(args.enable, args.database_url)
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)

    owns_db = db is None
    if owns_db:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from catalystiq.db.base import Base
        from catalystiq.db import models  # noqa: F401

        engine = create_engine(settings.database_url)
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()

    ingest_warnings: list[str] = []
    try:
        if args.ingest:
            ingest_warnings = _ingest(symbols, args.benchmark, db, start=start)
        report = run_history_validation(
            db, symbols=symbols, benchmark=args.benchmark, start=start, end=end,
            horizons=horizons, step_days=args.step_days, direction=args.direction,
            settings=settings, require_complete_history=not args.allow_incomplete_history,
            max_missing_ratio=args.max_missing_ratio, max_gap_sessions=args.max_gap_sessions,
            audit_only=args.audit_only, progress=True,
        )
    finally:
        if owns_db:
            db.close()

    report["ingest_warnings"] = ingest_warnings
    print(json.dumps(report, indent=2, default=str))
    if report["status"] == "audit_only":
        return 0 if report.get("all_symbols_complete") else 2
    if report["status"] == "incomplete_history":
        return 2
    return 0 if report.get("overall_sufficient") else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
