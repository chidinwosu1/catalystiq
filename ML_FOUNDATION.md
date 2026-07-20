# Catalyst IQ — ML Foundation (five-model system)

> **Status: DISABLED. Nothing here serves a user-facing prediction.**
> The entire subsystem is off by default, fails closed, and refuses to serve
> any prediction until validated, chronologically-tested, and **explicitly
> approved** model artifacts exist. No production inference, no frontend
> exposure, no merge without explicit approval.

This document describes the offline + online machinery for the five model
families, the safety rails around them, the validation design, and exactly
what remains before any model can be approved.

## The five model families

| Model | Responsibility | Module |
| ----- | -------------- | ------ |
| **Model 1** | Net-profit & target-before-stop probabilities (two separately calibrated heads) | `catalystiq/ml/models/model_one.py` |
| **Model 2** | Plausible net-return distribution (quantile regression q10–q90) | `catalystiq/ml/models/model_two.py` |
| **Model 3** | Path risk: adverse excursion, stop-breach & gap probability, tail loss | `catalystiq/ml/models/model_three.py` |
| **Model 4** | Cross-sectional stock opportunity ranker (replaces the hard-coded list) | `catalystiq/ml/models/model_four.py` |
| **Model 5** | Aggregate investor functional response (antecedent → response → consequence) | `catalystiq/ml/models/model_five.py` |

Model 4 and Model 5 are separate evidence sources; Model 5 never alters
Models 1–4 outputs, rankings, or trading status.

## Package layout

```
catalystiq/ml/
  flags.py                 fail-closed decision point for every capability
  features/
    schema.py              PointInTimeFeature + licensing/leakage gates
    provider.py            provider-NEUTRAL point-in-time interface (no direct provider calls)
    pit_provider.py        SilverPointInTimeProvider - concrete PIT features over validated Silver
    manifest.py            machine-readable feature-requirement manifest
  labels/
    costs.py               spread/slippage/fees/market-impact model (versioned)
    barriers.py            triple-barrier, MAE/MFE, stop-breach, gap, both-touch policy
    outcomes.py            versioned target definitions (net-profit, TBS, net return, …)
  dataset/
    builder.py             historical training-example builder (executable next-open entry)
    universe.py            point-in-time eligible-stock universe (Model 4)
  validation/
    splitter.py            purged, embargoed chronological walk-forward + final holdout
    leakage.py             look-ahead / purge / chronology / feature-target leakage checks
  calibration.py           sigmoid + isotonic, ECE, reliability bins
  evaluation/
    classification.py      ROC-AUC, PR-AUC, precision/recall/F1, Brier, log-loss, ECE
    quantile.py            pinball loss, coverage, median MAE, crossing detection
    ranking.py             precision@k, hit-rate, NDCG@k, Spearman, turnover, concentration
  models/                  base + heads + model_one..five + training orchestration
  reliability.py           reliability score/label/reasons + abstention gate
  governance.py            cross-model consistency + governed status
  ranking_governance.py    diversification guardrails + user-preference filtering
  registry.py              model-artifact registry service (approval gate)
  inference.py             unified inference-contract assembly (gated)
  schemas.py               stable Pydantic response contracts
catalystiq/routers/ml.py   disabled inference endpoints (fail closed)
catalystiq/db/models.py    MLModelArtifact registry table
alembic/versions/f9a2c1d4e8b7_add_ml_model_artifact.py
```

## Safety model — everything fails closed

`catalystiq/ml/flags.py` is the single decision point. Any error, missing or
invalid setting resolves to "not permitted".

| Flag | Default | Meaning |
| ---- | ------- | ------- |
| `ENABLE_ML` | `false` | master switch; everything else is gated on it first |
| `ENABLE_ML_TRAINING` | `false` | offline dataset build + fit + evaluate |
| `ENABLE_ML_INFERENCE` | `false` | online unified-contract assembly |
| `ENABLE_ML_RANKING` | `false` | Model 4 opportunity ranking |
| `ML_REQUIRE_APPROVED_MODELS` | `true` | only `approved` artifacts may serve (safety rail; true even if unreadable) |
| `ML_RANKER_REQUIRE_APPROVED_MODEL` | `true` | ranker requires approved M1–M3 + ranker |
| `ML_ALLOW_FRED_FEATURES` | `false` | FRED is blocked in the schema regardless (defense in depth) |
| `ML_ALLOW_TWELVE_DATA_TRAINING` | `false` | Twelve Data barred from training without a license flag |
| `ML_RANKER_MAX_HIGHEST_CONVICTION` | `4` | product cap on the Highest-Conviction section |
| `ML_RANKER_MAX_OPPORTUNITY_TABLE` | `25` | configurable table cap |
| `ML_RANKER_ALLOW_DEMO_DATA` | `false` | synthetic data may back unit tests only |
| `ENABLE_AGGREGATE_BEHAVIOR_MODEL` (+ `*_TRAINING`, `*_INFERENCE`, `*_ALLOW_FRED`, `*_ALLOW_TWELVE_DATA_TRAINING`, `*_REQUIRE_APPROVED_ARTIFACT`, `*_ALLOW_DEMO_DATA`) | `false`/`true` | Model 5 gates, mirroring the core rails |

Enabling a flag never approves a model. Approval is a deliberate registry
action (`catalystiq/ml/registry.py::approve`) that refuses synthetic-data
artifacts and artifacts with no evaluation metrics.

## Feature schema, licensing & leakage

Every feature is a `PointInTimeFeature` carrying `symbol`,
`prediction_timestamp`, `feature_name`, `feature_value`, `source_provider`,
`source_event_timestamp`, `available_at_timestamp`, `retrieved_at_timestamp`,
`data_quality_status`. Rejected when:

- `available_at_timestamp > prediction_timestamp` (look-ahead) — hard error;
- provider is **FRED** (blocked outright) or an unlicensed alt source
  (behavioral/sentiment/news/unknown);
- provider is **Twelve Data** and the use is *training* without the license
  flag;
- any provenance field is missing.

The ML foundation consumes a **provider-neutral** interface
(`features/provider.py`) — it does not call Yahoo/Twelve Data/SEC/BLS/BEA
directly, and it does not modify those integrations. Where a required feature
has no wired point-in-time source, that gap is recorded in the
machine-readable manifest (`catalystiq/ml/feature_requirements.json`) — never
fabricated.

### Concrete point-in-time provider (`features/pit_provider.py`)

`SilverPointInTimeProvider` is the first real implementation. It reads the
app's **own validated Silver bars** (via `get_silver_bars`) and the existing
analysis snapshots + the published `build_opportunity_score` contract — no
external provider calls, no integration changes. All computation runs on bars
truncated to the **last closed session at or before `prediction_timestamp`**,
so the feature vector is **look-ahead invariant**: identical whether or not
future bars exist in the database (asserted in tests). `get_executable_entry`
returns the *next* session's open (offline only; `None` at live inference).

Wired now (41 features): adjusted OHLCV, trend/MA, momentum, RSI/MACD,
volatility/ATR, volume/relative-volume, liquidity/estimated-spread, gaps,
**support/resistance distances** (nearest active level from the market-structure
snapshot on point-in-time bars), market/sector, relative strength, beta,
**market regime** (a versioned, deterministic trend×volatility classifier over
point-in-time benchmark bars, `features/regime.py`), **point-in-time SEC
fundamentals** (revenue YoY, gross margin, recent-filing flag — see below), the
Rule-Based Opportunity Score and its factor sub-scores, missingness indicators,
and data-quality/freshness. Still recorded as gaps (MISSING, never fabricated):
earnings proximity, and the BLS/BEA macro features (see the vintage note below).

### Point-in-time vintages: SEC, BLS, BEA (`features/fundamentals_pit.py`, `features/macro_pit.py`)

These use **only the value/amendment released as of the prediction timestamp —
never a later revision**:

- **SEC fundamentals (truly point-in-time, wired).** XBRL facts are eligible
  only if `filing_date <= as_of`; among eligible vintages of one
  (concept, unit, period) the latest `filing_date` wins, so an amendment that
  *was* public supersedes the original while one filed later is invisible.
  `pit_revenue_yoy`, `pit_gross_margin`, and `recent_filing_event` are computed
  with per-feature provenance (`available_at` = the governing filing date).
  Fails closed (MISSING) when the CIK or required periods are absent.
- **BLS (fails closed).** BLS observations carry no realtime vintage
  (`realtime_start` is null; a revision overwrites in place), so a legitimate
  as-of value cannot be established — `macro_cpi_yoy_pit` is MISSING. The
  vintage read is written correctly and lights up automatically if a
  vintage-preserving ingestion lands (a test proves the read against seeded
  vintages, and that a future-dated revision is excluded).
- **BEA (fails closed).** BEA values are stored current-state only (no realtime
  dimension), so `macro_gdp_qoq_pit` is MISSING — the possibly-revised stored
  value is never read.
- **FRED** remains BLOCKED from ML features entirely.

## Labels & executable entry

For end-of-day analysis: `prediction_timestamp = session close`,
`simulated_entry = next session's executable open`. Entry is never assumed at
a price already required to compute the prediction. Net outcomes subtract
spread + slippage + fees + estimated market impact. A candle touching **both**
target and stop is handled conservatively — either excluded or counted as
**stop first**, never target first.

## Chronological validation

`validation/splitter.py` provides purged, embargoed walk-forward folds with a
single untouched final holdout. Training samples whose outcome window reaches
into the calibration/validation region are **purged**; an **embargo** drops
samples near each boundary. Random splits are never used. Preprocessing
(imputation, winsorization, scaling) is fit on the training fold only
(`models/base.py::Preprocessor`).

## Reliability, governance & abstention

- **Reliability** (`reliability.py`) is a 0–100 index — *not* a probability —
  built from feature completeness, freshness, comparable sample count, OOD
  status, calibration, recent OOS performance, regime representation, range
  width and model agreement. It abstains (`insufficient_evidence` / `abstain`)
  on missing artifacts, stale/thin data, OOD setups, calibration or quantile
  failure, or conflicting outputs.
- **Cross-model governance** (`governance.py`) flags material conflicts (e.g.
  high profit prob + negative median; high TBS prob + high stop-breach; low
  predicted risk in extreme realized vol) and blocks high-conviction results.
  Governed status ∈ `enter_candidate | watch | wait | avoid | abstain |
  insufficient_evidence`. `enter_candidate` is **not** trade authorization —
  the existing Review → Confirm order controls remain required.
- **Ranking governance** (`ranking_governance.py`) applies diversification
  guardrails then user-preference filtering as separate, auditable stages,
  preserving `raw_rank`, `governed_rank`, and every exclusion reason.

## Unified inference contract

`GET /ml/inference/{symbol}` returns the stable five-model contract, or
`{"status": "not_available", ...}` while disabled/unapproved.
`GET /ml/ranking` and `GET /ml/behavior/{symbol}` similarly return unavailable
states. `GET /ml/status`, `/ml/feature-requirements`, and `/ml/registry`
expose non-sensitive metadata. No endpoint ever returns placeholder
probabilities or demo values.

## Chronological training dry-run (`ml/dry_run.py`)

`run_training_dry_run(...)` exercises the whole offline path end-to-end —
`SilverPointInTimeProvider` → `TrainingExampleBuilder` → chronological split +
purged walk-forward + leakage checks → feature-coverage & label diagnostics →
(optionally) fit candidate Models 1–3 → a **sufficiency verdict** — to answer
"are the wired point-in-time features and available history actually sufficient
to train?" *before* any model is approved.

It is a training-side tool and **fails closed**: it refuses to run unless
`ENABLE_ML` + `ENABLE_ML_TRAINING` are set in the passed settings, model fitting
additionally needs scikit-learn, and it only ever registers **candidate**
(never approved) artifacts — synthetic-data runs can never be promoted. It
accepts either a symbol set + prediction dates (builds from validated Silver) or
a pre-built dataset. The report includes fold purge/embargo counts, any leakage
findings, per-feature-group coverage, label class balance / return variance, and
a structured `sufficiency` block with human-readable notes (including the
expected always-missing groups: earnings, macro).

An operator CLI wraps it for real-data runs (in an environment with market-data
access):

```
python -m catalystiq.ml.dry_run_cli \
    --symbols AAPL,MSFT,NVDA,JPM,XOM --benchmark SPY \
    --start 2020-01-01 --end 2021-06-30 --horizon 5 --enable --ingest
```

`--enable` turns on `ENABLE_ML`+`ENABLE_ML_TRAINING` for that process only (no
persisted config change, no inference/serving/approval); `--ingest` refreshes
each symbol's Silver via the app's own pipeline first, reporting (never faking)
any per-symbol fetch failure. It prints the `DryRunReport` JSON and exits
non-zero when the data is not yet sufficient for training.

## What remains before any model can be approved

1. **Real point-in-time data wiring** — *price-derived + rule-based groups are
   now wired* via `SilverPointInTimeProvider` (validated Silver bars + analysis
   snapshots + rule-based Opportunity Score + market-regime classifier +
   **point-in-time SEC fundamentals** with amendment/vintage handling). The
   **BLS/BEA vintage reads are implemented and fail closed** until a
   vintage-preserving ingestion lands. Remaining before full-feature training:
   a point-in-time earnings calendar (needs a licensed, timestamped feed),
   vintage-preserving BLS/BEA ingestion (to activate the already-written macro
   reads). Support/resistance distances are now wired. See
   `feature_requirements.json` for the live status of each group. The only
   `unavailable` group left is earnings proximity — it genuinely needs a
   licensed, timestamped feed and is never fabricated or substituted.
2. **A real historical dataset** — successful/unsuccessful/**delisted**
   securities, point-in-time universe membership, corporate-action
   adjustment, cost estimates, full provenance. No user-facing artifact may be
   trained on synthetic/demo data.
3. **Chronological training + evaluation runs** with the walk-forward
   splitter, reporting the full metric batteries per horizon/sector/regime/
   liquidity/direction.
4. **Serving loader** for approved serialized artifacts (intentionally not
   wired yet — inference fails closed even if artifacts were approved).
5. **Explicit human approval** in the registry, per family/direction/horizon.
   Long-only first; short models stay unavailable until separately validated.

Until all of the above, `ENABLE_ML` and every stage flag stay `false`.
