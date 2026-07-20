# Catalyst IQ — Historical Model Validation + MLflow Tracking

> **Still DISABLED for serving.** This phase adds an **offline** training and
> evaluation runner that records a full experiment to MLflow. It does **not**
> enable production inference, frontend predictions, model approval, registry
> promotion or order submission. Training authorization applies to the CLI
> process only, and only when you pass `--enable-training`.

This builds directly on the existing point-in-time dataset, the fail-closed
dry-run sufficiency harness, the purged/embargoed walk-forward splitter and the
Model 1–5 implementations. It introduces **no** second feature pipeline and
re-implements **no** model — it wires the existing pieces together and logs the
whole experiment.

## What the runner does

For each requested horizon, `catalystiq/ml/experiment.py::run_experiment`:

1. builds the point-in-time training dataset (via the existing provider +
   `TrainingExampleBuilder`), optionally after ingesting real history;
2. runs the existing **sufficiency / chronology / leakage / provenance /
   point-in-time** checks and **refuses to train** that horizon if any fail;
3. trains **Models 1–3** with the existing purged, embargoed walk-forward
   validation (each head selects candidate-vs-baseline on the untouched final
   holdout);
4. generates **out-of-fold** Model 1–3 predictions and only then trains
   **Model 4** (the ranker refuses in-sample inputs); builds **Model 5**'s
   aggregate-response tables as a separate evidence source;
5. evaluates candidates against their **simple baselines** and the
   **deterministic Catalyst IQ rule-based scorer**, then evaluates **once** on
   the untouched final holdout after selection — slicing by sector, market
   regime, confidence band and holding period, and reporting metrics **after
   transaction costs and slippage**;
6. logs everything to a **parent MLflow run** with nested child runs per model,
   horizon, fold and candidate algorithm.

Run structure in MLflow:

```
experiment:<name>                     (parent run)
└─ horizon:5d
   ├─ model_1:5d
   │  ├─ model_1:5d:fold_0 … fold_n   (walk-forward stability)
   │  ├─ model_1:net_profit:candidate (baseline vs candidate algorithm)
   │  └─ model_1:target_before_stop:candidate
   ├─ model_2:5d  (+ folds, candidate)
   ├─ model_3:5d  (+ folds, candidates)
   ├─ oof:models_1_3                   (out-of-fold coverage + holdout isolation)
   ├─ model_4:5d                       (ranker; OOF inputs only)
   └─ model_5:5d                       (aggregate response — separate evidence)
```

### Logged metrics & params (per run, where applicable)

Dataset hash + version; feature- and label-contract versions; code/git commit;
symbols, benchmark, date range, horizon, sample counts; train/validation/
holdout boundaries; purge + embargo settings; feature coverage + missingness;
class balance + label distributions; hyperparameters + seeds; ROC-AUC, PR-AUC,
Brier, log-loss, ECE + calibration status; pinball loss + interval coverage;
NDCG + Spearman + precision@k; realized after-cost trading metrics; results
sliced by sector / market regime / confidence band / holding period;
baseline-vs-candidate comparisons; and the leakage/sufficiency findings +
acceptance-threshold verdicts.

### Logged artifacts

`dataset_manifest.json`, `provenance_summary.json`, `feature_manifest.json`,
`gate_verdict.json`, `fold_definitions.json`, calibration/reliability plots,
confusion matrices, ROC and precision-recall plots, predicted-vs-actual and
quantile-coverage plots, feature importance (only when a GBDT candidate is
chosen), `model_comparison_report.json`, per-model `failure_cases.json`,
`model_4/ranking_report.json`, `model_5/aggregate_response_tables.json`, and the
experiment-level `final_holdout_report.json`.

## Configuration (no hard-coded credentials)

MLflow is configured entirely through the environment / settings:

| Setting | Env var | Default | Meaning |
| ------- | ------- | ------- | ------- |
| `mlflow_tracking_uri` | `MLFLOW_TRACKING_URI` | *(blank)* | Blank → local `mlruns`. Point at a server for a shared backend. |
| `mlflow_experiment_name` | `MLFLOW_EXPERIMENT_NAME` | `catalystiq-ml-validation` | Experiment to record under. |
| `mlflow_local_dir` | `MLFLOW_LOCAL_DIR` | `mlruns` | Local dir used when no tracking URI is set. |

Leaving the tracking URI blank uses a local `mlruns` directory. A remote
server's auth is supplied via MLflow's **own** environment variables — never in
this repo.

---

## Commands to run locally (e.g. on your Mac from the repo root)

### 1. Install dependencies and start the MLflow dashboard

```bash
python -m pip install -r requirements.txt

# Recent MLflow versions gate the local file store; opt in for `mlruns`:
MLFLOW_ALLOW_FILE_STORE=true mlflow ui --port 5000
# (older MLflow: `mlflow ui --port 5000` is enough)
```

Then open the dashboard at:

```
http://127.0.0.1:5000
```

### 2. In a second terminal — run the SMOKE TEST first

A small universe + short range that validates the wiring quickly. **This is a
smoke test, not validation** — do not treat a five-symbol, one-year run as
proof a model works:

```bash
python -m catalystiq.ml.train_cli \
  --symbols AAPL,MSFT,SPY \
  --benchmark SPY \
  --start 2020-01-01 \
  --end 2021-06-30 \
  --horizons 5 \
  --ingest \
  --enable-training \
  --smoke-test
```

### 3. Then run the full experiment

```bash
python -m catalystiq.ml.train_cli \
  --symbols AAPL,MSFT,NVDA,JPM,XOM,SPY,QQQ \
  --benchmark SPY \
  --start 2015-01-01 \
  --end 2026-06-30 \
  --horizons 1,5,10,20 \
  --ingest \
  --enable-training
```

Refresh `http://127.0.0.1:5000` to compare models, horizons and folds, and to
see which candidates beat their simple baselines **and** the deterministic
Catalyst IQ scorer on the untouched holdout.

> **On genuine validation.** A smoke run on a handful of large-cap survivors
> over a single regime can make the MLflow charts look impressive while the
> model is unreliable. Real validation needs a **broad universe, multiple
> market regimes, a long history, and ideally delisted names** so survivorship
> bias does not flatter the results. The full command above widens the universe
> and the date range, but assembling a delisting-aware universe with
> point-in-time membership is the remaining data-side work (see
> `ML_FOUNDATION.md`).

### Flags

- `--enable-training` — **required**. Turns on `ENABLE_ML` + `ENABLE_ML_TRAINING`
  for this process only. Without it the runner fails closed.
- `--ingest` — refresh each symbol's Silver via the app's own pipeline first. A
  per-symbol fetch failure (network policy / rate limit) is reported and
  skipped, never faked.
- `--register-candidates` — register fitted models as **candidate** artifacts
  (never approved; synthetic datasets are flagged and can never be promoted).
- `--experiment-name`, `--horizons`, `--step-days`, `--direction`,
  `--database-url`, `--output-dir`, `--seed` — see `--help`.

The process exits non-zero when no horizon passed its sufficiency gate (nothing
was trainable yet) — so a thin/insufficient dataset surfaces honestly instead
of producing a fake “success”.

## What stays off

No production inference, no frontend predictions, no model approval, no registry
promotion, no order submission. Approval remains a separate, deliberate registry
action (`catalystiq/ml/registry.py`) that this runner never performs.

## Known gap: earnings proximity

`earnings_proximity` has no licensed, timestamped point-in-time feed, so it is
recorded as **missing** (never fabricated). Per the versioned feature contract,
that single missing group does not invalidate the price-derived, rule-based,
fundamentals or macro models that do not require it.

## Tests

Deterministic, seeded, MLflow-mocked tests cover the runner:

```bash
python -m pytest tests/test_ml_tracking.py tests/test_ml_oof.py \
  tests/test_ml_experiment.py tests/test_ml_train_cli.py -q
```

They assert MLflow logging, parent/child run structure, reproducibility,
out-of-fold enforcement, final-holdout isolation, fail-closed + gate-refusal
behavior, and artifact creation — all on clearly-labeled synthetic fixtures
(synthetic data is unit-test only and can never be approved).
