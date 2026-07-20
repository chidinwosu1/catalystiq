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

First generate a broad universe (current S&P 500 constituents), then train on it
with `--symbols-file`:

```bash
# writes sp500_universe.txt (one ticker per line; header carries the caveat)
python -m catalystiq.ml.universe_sp500 --out sp500_universe.txt

python -m catalystiq.ml.train_cli \
  --symbols-file sp500_universe.txt \
  --benchmark SPY \
  --start 2015-01-01 \
  --end 2026-06-30 \
  --horizons 1,5,10,20 \
  --ingest \
  --enable-training
```

`--symbols` and `--symbols-file` can be combined (e.g. add `--symbols SPY,QQQ` for
benchmarks); tickers are de-duplicated and normalized to the provider convention
(`BRK.B` → `BRK-B`). A full S&P 500 × 11-year × 4-horizon run ingests a lot of
history and takes a while on the free provider (per-symbol fetch failures are
reported in `ingest_warnings`, never faked) — start smaller and scale up.

> **Survivorship bias (important).** `universe_sp500.py` uses **current** index
> membership, so it contains only companies still in the S&P 500 today — every
> dropped/delisted/bankrupt name is missing. That makes results **optimistic**.
> The runner repeats this caveat in `data_source_caveats.json`, in the MLflow
> tags/params (`survivorship_bias`), and on stderr. Genuinely unbiased
> validation needs point-in-time membership + delisted names from a licensed
> dataset (CRSP / Norgate / Sharadar / Polygon), which this free path cannot
> provide.

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

---

## Running it on Render (no terminal — click-by-click)

**Determination — is this safe as a one-time Render job?** Yes. The runner is
offline and fail-closed: it approves nothing, serves nothing, and never enables
inference or order submission. The only real caveats are operational, and the
config below handles them:

- **Ephemeral filesystem.** Render's disk is wiped when a job ends, so results
  are logged to a **dedicated MLflow Postgres database** (separate from the app
  DB) that persists them.
- **Isolation.** Training ingests price history into an **ephemeral SQLite**
  file, never the production app database.
- **Free-tier limits.** A full S&P 500 × 11-year run is too heavy for free
  compute + free-provider rate limits, so the job ships with a **bounded**
  default universe/date range. Widen it (and use a larger instance) for a
  stronger run.
- **Public UI.** MLflow's dashboard has no built-in auth; keep it semi-private
  and **suspend it when not viewing** (see the security note in `render.yaml`).

`render.yaml` already defines everything: a dedicated `catalystiq-mlflow-db`, a
free `catalystiq-mlflow-ui` web service (the dashboard), and a manually-triggered
`catalystiq-ml-training` cron job. Training stays disabled by default — the job's
command opts in with `--enable-training` for that one process only; no
environment flag enables ML.

**Steps (all in the Render dashboard):**

1. **Sync the blueprint.** In your Render account → **Blueprints** → your
   Catalyst IQ blueprint → **Apply/Sync** the latest `render.yaml`. This creates
   the `catalystiq-mlflow-db` database, the `catalystiq-mlflow-ui` web service,
   and the `catalystiq-ml-training` cron job. (The cron uses a low-cost instance
   billed only while it runs; it stays idle until you trigger it.)
2. **Open the dashboard once** so MLflow initializes its tables: click
   **catalystiq-mlflow-ui** → open its `…onrender.com` URL. It'll be empty for
   now.
3. **Run the training job.** Click **catalystiq-ml-training** → **Trigger Run**.
   Watch the **Logs** tab — it prints progress, the survivorship warning, and
   the final JSON report. (Optionally edit the job's **Start Command** first to
   widen `--symbols`/`--start`/`--end` for a stronger run.)
4. **View results.** Refresh the **catalystiq-mlflow-ui** URL. Open the
   `catalystiq-ml-validation` experiment and compare runs — sort by
   `holdout.roc_auc`, check `holdout.expected_calibration_error`,
   `acceptance.beats_deterministic_scorer`, the per-sector/regime metrics, and
   the after-cost `trading.*` numbers.
5. **Clean up.** When done, **Suspend** (or delete) both `catalystiq-mlflow-ui`
   and `catalystiq-ml-training` so nothing keeps running.

Notes: on the free/database-only setup, numeric metrics/params/tags and run
structure persist and render in the UI; plot/JSON **artifacts** aren't persisted
(they also print in the job's run logs). To keep artifacts too, attach a
persistent disk or object store and run `mlflow server` with
`--artifacts-destination` + `--serve-artifacts` — see the comments in
`render.yaml`. Nothing here approves, promotes, deploys or serves a model.

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
  tests/test_ml_experiment.py tests/test_ml_train_cli.py tests/test_ml_universe.py -q
```

They assert MLflow logging, parent/child run structure, reproducibility,
out-of-fold enforcement, final-holdout isolation, fail-closed + gate-refusal
behavior, and artifact creation — all on clearly-labeled synthetic fixtures
(synthetic data is unit-test only and can never be approved).
