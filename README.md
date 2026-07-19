# Catalyst IQ

Behavioral market intelligence app: pulls market data, runs a deterministic
analytical engine (technical/options/fundamentals/institutional/sentiment)
plus a behavioral (Functional Behavioral Analysis) engine over it, and
synthesizes the result into a structured, probabilistic per-ticker report.
See the build spec for the full architecture and phase plan.

**This tool produces probabilistic, educational market analysis — not
investment advice, and not a guarantee of any outcome.**

## Build status

This codebase currently implements **Phase 1 — Data plumbing**:

- `MarketDataProvider` interface (`catalystiq/providers/market_data.py`),
  with a Yahoo Finance implementation (`yfinance`).
- `BrokerProvider` interface (`catalystiq/providers/broker.py`). The active
  broker flow is always:

  ```
  Catalyst IQ backend -> BrokerProvider -> WebullBroker -> Webull Trading API
  ```

  `WebullBroker` is a real integration against the official
  `webull-openapi-python-sdk`, matching the build spec's original target,
  and is the **sole** broker the application constructs - `get_broker_provider()`
  rejects any `BROKER_PROVIDER` value other than `webull` with a clear
  `BrokerError` (502) rather than falling back to anything else. The order
  write-path (place/replace/cancel/detail/open) is fully implemented and
  verified against the SDK's real source and Webull's own docs;
  `get_account()`/`get_positions()` deliberately raise rather than guess at
  Webull's balance/position JSON field names, which this build couldn't
  verify — see `WebullBroker`'s docstring and use `get_account_balance_raw()`
  / `get_positions_raw()` in the meantime. (`AlpacaPaperBroker` also still
  exists in the same module as a disabled legacy adapter, kept only so its
  own unit tests keep running - it's never constructed by the running
  application.)
- Postgres schema (`catalystiq/db/models.py`, migrated with Alembic) matching
  the spec's schema sketch, plus the medallion tables below: `tickers`,
  `options_snapshots`, `news_events`, `behavioral_events`,
  `reinforcement_stats`, `reports`.
- The Data Validation Layer (`catalystiq/validation/data_quality.py`):
  chronological-order check, dedupe, missing-trading-day detection, abnormal
  price-gap flagging (z-score), and a live-quote cross-check — run before
  any bar is persisted as "ready for rules."
- A **Bronze -> Silver -> Gold medallion pipeline** for the price-bar domain
  (`catalystiq/pipelines/market_price_pipeline.py`) - the only domain with a
  real, working ingestion path today. Implemented as plain PostgreSQL/SQLite
  tables with prefixed names (no Postgres schema objects, since the whole
  test suite runs on SQLite), not a distributed lakehouse:

  ```
  Providers -> Bronze -> Silver -> Gold -> API/UI
  ```

  - **Bronze** (`bronze_ingestion_run`, `bronze_market_price_bar`):
    source-aligned, minimally-transformed OHLCV exactly as
    `MarketDataProvider.get_ohlcv()` returned it, with an ingestion-run audit
    trail. Append-only - a routine re-ingest never overwrites a prior run's
    rows. Written by `ingest_bronze()`.
  - **Silver** (`silver_price_bar`, `silver_price_bar_rejected`): reads only
    from Bronze (plus an optional live quote from an approved real-time
    adapter for the cross-check), runs the existing Data Validation Layer,
    and upserts cleaned bars keyed on ticker+date - idempotent, so
    reprocessing the same Bronze run reproduces the same Silver state. Bars
    with an invalid OHLC relationship are quarantined into
    `silver_price_bar_rejected` rather than dropped; bars with other issues
    (abnormal gaps, thin history, ...) stay in Silver flagged
    `data_quality_status="flagged"`. Written by `build_silver()`.
  - **Gold** (`gold_technical_snapshot`, `gold_market_structure_snapshot`,
    `gold_risk_snapshot`, `gold_volume_liquidity_snapshot`,
    `gold_market_context_snapshot`): reads only from Silver via
    `get_silver_bars()` - never touches a provider - calls the existing pure
    compute functions in `catalystiq/analysis/*.py` unchanged, and persists
    a versioned row (`calculation_version` + full lineage: Silver record
    count/date range, Bronze ingestion run id, source provider, calculated-
    at) keyed on ticker+date+calculation_version. Written by the five
    `build_gold_*()` functions.
  - `ensure_fresh()` is the only place a router-triggered flow is allowed to
    touch the provider: it runs Bronze->Silver on demand if Silver has no
    data for a symbol or it's older than 24h, otherwise no-ops. Every
    `GET /analysis/...` endpoint calls it before reading Gold, so searching
    an unseen ticker still "just works" without a separate explicit ingest
    step - the on-demand ingest is the only provider touchpoint; the Gold
    compute functions themselves never call a provider.
- `POST /market-data/ingest/{symbol}` now runs `ingest_bronze()` then
  `build_silver()` (same response shape - a `DataQualityReport`).

There's also a working slice of **Phase 7 — Frontend** (`frontend/`): a
React + Vite + Tailwind app with four tabs:

- **Trade** — a real trade ticket wired to the broker (§1.1 Execution
  Zone): live quote/company name, all five order types (market, limit,
  stop, stop-limit, trailing stop), real bracket/OTO take-profit and
  stop-loss legs, review → submit against `/paper/orders`.
- **Portfolio** — real account/positions data from `/paper/account` and
  `/paper/positions` (total value, cash, buying power, today's/total P/L,
  per-position table with Buy More/Sell/Analysis actions). The "Portfolio
  Intelligence" section (sector exposure, beta, correlation) is clearly
  labeled demo data - it needs analytics this build doesn't compute yet.
- **Markets** — a full Market Intelligence dashboard (index overview,
  sector rotation, catalysts, daily watchlist), entirely demo data since
  the Market Environment/Sector/News modules aren't built, and labeled as
  such throughout.
- **Analysis** — real live price for a searched ticker; the setup
  indicators, Catalyst IQ scores, and rating are demo data (reusing the
  `AnalysisCard` building blocks) pending the analytical engine. Below
  that, a real **trade journal** (entry/exit, thesis, exit reason, rules
  followed) and **performance analytics** (win rate, profit factor,
  best/worst trade, performance by trade type) computed live from
  whatever you log - not persisted to the backend yet, so it resets on
  reload.

Every demo-data section is visibly marked (a small "Demo data" badge) so
it's never confused with real output.

Everything past this (indicator/regime/scoring modules, the FBA engine, LLM
synthesis, the rest of the frontend, backtesting) is **not yet built** — see
the build spec's phase list for what's next.

## Project layout

```
catalystiq/
  config.py          # env-driven settings (Settings/get_settings)
  auth.py             # shared bearer-token dependency for action endpoints
  main.py              # FastAPI app assembly
  db/
    base.py             # engine/session (defaults to local SQLite)
    models.py            # ORM models (§7 schema)
  providers/
    base.py               # provider vocabulary: DataDomain, DataClassification,
                           # IngestionStatus, ProviderError/category, adapter identity
    registry.py            # data-driven source registry + config-gated factory
    transport.py           # shared HTTP client: timeouts, retry+backoff+jitter,
                            # token-bucket rate limiter, circuit breaker, secret redaction
    market_data.py       # MarketDataProvider ABC + YahooFinanceProvider
    broker.py              # BrokerProvider ABC + WebullBroker (sole active broker;
                            # AlpacaPaperBroker also lives here as a disabled legacy adapter)
  schemas/                # Pydantic request/response/domain shapes
  validation/
    data_quality.py        # Data Validation Layer (§2.9)
  analysis/                  # pure compute functions for each Gold product
  pipelines/
    market_price_pipeline.py # Bronze -> Silver -> Gold for the price-bar domain
  routers/
    broker.py               # /paper/* (account, positions, orders)
    market_data.py            # /market-data/* (quote, ohlcv, ingest, ...)
    analysis.py                # /analysis/* (Gold-layer reads, on-demand ingest)
alembic/                      # schema migrations
tests/                         # pytest suite (offline; provider calls are mocked)
app.py                          # deployment entrypoint, re-exports catalystiq.main:app
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # if present; otherwise set the vars below directly
```

Environment variables (`catalystiq/config.py`):

| Variable | Purpose | Default |
|---|---|---|
| `ACTION_API_KEY` | Bearer token required on all `/paper/*` and `/market-data/*` endpoints | — (required) |
| `BROKER_PROVIDER` | Which `BrokerProvider` to use. Webull is the only supported value - anything else is rejected with a `BrokerError` (502), no fallback | `webull` |
| `WEBULL_APP_KEY` / `WEBULL_APP_SECRET` / `WEBULL_ACCOUNT_ID` | Webull OpenAPI credentials ([apply here](https://developer.webull.com/apis/docs/authentication/apply/); shared test accounts also work without applying) | — (required) |
| `WEBULL_REGION_ID` | Webull region, e.g. `us` or `hk` | `us` |
| `WEBULL_API_ENDPOINT` | Override the SDK's resolved endpoint (e.g. to pin the sandbox host) | — (SDK default) |
| `WEBULL_TOKEN_DIR` | Where the SDK stores its 2FA token after the first call | — (SDK default, `conf/token.txt`) |
| `DATABASE_URL` | SQLAlchemy URL. Defaults to a local SQLite file for dev; point at Postgres in production | `sqlite:///./catalystiq.db` |
| `MARKET_DATA_PROVIDER` | Which `MarketDataProvider` to use | `yahoo` |
| `PRICE_GAP_ZSCORE_THRESHOLD` | Abnormal-gap flag threshold | `3.0` |
| `PRICE_HISTORY_LOOKBACK_YEARS` | Target history depth for the thin-history confidence flag | `5` |
| `CORS_ALLOW_ORIGINS` | Comma-separated origins allowed to call the API from a browser | `http://localhost:5173,http://127.0.0.1:5173` |

Apply migrations (creates the tables in `DATABASE_URL`):

```bash
python -m alembic upgrade head
```

Run the API:

```bash
uvicorn app:app --reload
```

Run tests (fully offline — provider network calls are mocked):

```bash
python -m pytest
```

### Frontend

```bash
cd frontend
npm install
cp .env.example .env   # points the frontend at the backend + dev API key
npm run dev
```

`frontend/.env`'s `VITE_ACTION_API_KEY` must match the backend's
`ACTION_API_KEY` for the live-lookup search to authenticate. See the
warning in `frontend/src/lib/api.ts` — this is a dev-only auth shortcut,
not a pattern to ship.

## API surface (Phase 1)

- `GET /market-data/quote/{symbol}`
- `GET /market-data/ohlcv/{symbol}?days=365`
- `GET /market-data/fundamentals/{symbol}`
- `GET /market-data/news/{symbol}?limit=10`
- `POST /market-data/ingest/{symbol}?days=1825` — fetch, validate, persist;
  returns a `DataQualityReport`.
- `GET /paper/account`, `GET /paper/positions`, `GET /paper/orders`,
  `POST /paper/orders`, `GET /paper/orders/{id}`, `DELETE /paper/orders/{id}`

All of the above require `Authorization: Bearer <ACTION_API_KEY>`.

## Data-source integration (foundation)

External data always flows **Provider → Bronze → Silver → Gold**; Gold
compute functions never call a provider directly (enforced today in
`pipelines/market_price_pipeline.py`). The `providers/` package is the
foundation for extending this beyond the market-price domain:

- **`base.py`** — one shared vocabulary every adapter uses: `DataDomain`,
  `DataClassification` (real-time / delayed / end-of-day / revised),
  `IngestionStatus` (`running`/`succeeded`/`partial`/`failed`/`rate_limited`/
  `unavailable`), `LicenseClassification`, and a normalized
  `ProviderError` + `ProviderErrorCategory`. Adapters declare identity via
  `PROVIDER_NAME` / `ADAPTER_VERSION` / `DOMAIN`.
- **`registry.py`** — every planned source described as data (domain,
  required setting *names*, enable flag, license class, base URLs,
  `implemented`), plus `build_adapter(name)`: a single config-gated factory
  that raises a `CONFIG`-category `ProviderError` for an unknown, disabled,
  unconfigured, or not-yet-implemented source instead of failing obscurely.
- **`transport.py`** — the shared HTTP client for the REST-based adapters
  added in later phases (SEC, FRED, BLS, BEA, FINRA, Nasdaq, Twelve Data):
  explicit connect/read timeouts, bounded retries with exponential backoff +
  jitter, a token-bucket rate limiter, a circuit breaker, and secret
  redaction. Fully unit-testable (clock/sleep/jitter injected) — no live
  calls in the suite.

`BronzeIngestionRun` now carries domain-agnostic ingestion fields (dataset,
endpoint, requested identifier, response/release timestamps, HTTP status,
record count, rate-limit info, retry count, error category, payload checksum
/ reference, license class) so one table records ingestion for every domain.

**Configuration.** Each source has an `ENABLE_*` flag and, where needed, an
API key; a key is required only when its source is enabled *and* its adapter
is implemented. `validate_settings()` runs at startup and raises a
`ConfigurationError` naming any missing setting — **names only, never
values**. See `.env.example` for the full list (placeholders only; real
secrets belong in your local `.env` / host environment, never in git).

All ten sources are implemented: **Yahoo Finance** (market data, initial
primary), **Twelve Data** (optional secondary / validation, off by default),
**Webull** (brokerage; read-only), **NYSE** market calendar, **FRED/ALFRED**
+ **BLS** + **BEA** (macro), **SEC EDGAR** (fundamentals), and **FINRA** +
**Nasdaq Trader** (regulatory).

**Cross-provider validation (§5, §16).** When Twelve Data is enabled, the
compare endpoint fetches the same quote from Yahoo (primary) and Twelve Data
(secondary) and records a `provider_comparison` row. Values are never averaged
and the secondary never silently overwrites the primary.
`POST /data-quality/market_data/compare/{symbol}`, `GET /data-quality/{domain}`.

**Twelve Data is restricted personal-use (compliance).** It is optional and off
by default. Its plan credit limits (8/min, 800/day, Basic) are enforced
centrally with per-endpoint credit weights; it auto-shuts-off on the daily cap
or on credential/licensing failure; and its **raw values are never persisted** —
the comparison record keeps only the within/outside-tolerance outcome and
provenance, never a value or reconstructable difference. It never feeds any
model, score, or backtest. See **`TWELVE_DATA_COMPLIANCE.md`**.

**Order submission is disabled by default (§13).** Paper and live are
separate flags with separate credentials; **live is refused until separately
approved** (even if its flag is set). When paper submission is enabled, every
order is a two-step flow: `POST /paper/orders/confirm` returns the exact
details to review (symbol, side, qty/notional, type, limit/stop, **estimated
max loss**, account) plus a **single-use, short-lived confirmation token**
bound to those details; `POST /paper/orders` submits only with a valid token
and consumes it — any change to the order invalidates it. The scheduled-order
poller **never auto-submits**: it flips a due order to `due` for manual
review. `GET /paper/connection-test` is a read-only Webull reachability check.

**Health/admin surfaces (§18):** `GET /data-sources`, `/data-sources/health`,
`/data-sources/{provider}/health` — enabled/configured state, last successful
ingestion, last failure category, and freshness, with **setting names only,
never secret values**.

New network/document domains flow through a generic Bronze store
(`BronzeRawDocument`) and the `pipelines/ingestion.py` helpers, then into
normalized Silver products that all share the `SilverRecordMixin` common
columns: `silver_market_session`, `silver_macro_series` /
`silver_macro_observation` / `silver_economic_release`,
`silver_security_identifier` / `silver_company_filing` /
`silver_company_fact` / `silver_material_event`. Point-in-time is preserved:
a macro observation's vintage window is part of its identity, so a revision
is a new row and the originally-known value is never overwritten; likewise an
amended SEC filing's facts are preserved alongside the originals, with the
latest-filed value surfaced as active.

Read-only endpoints: `GET /market-calendar/sessions`,
`GET /macro/series/{id}/observations?source=bls`, `GET /macro/bea?table=`,
`GET /fundamentals/{symbol}`, `GET /filings/{symbol}`,
`GET /short-interest/{symbol}`, `GET /short-sale-volume/{symbol}`.

**FRED is a special case (compliance).** It is *not* served through the macro
Silver layer: it is an isolated, ephemeral, allowlisted macro-context source
that is never persisted, cached, logged, or fed to any score/model/order path.
It lives behind its own `GET /fred/context` (and `/fred/series`) endpoints,
served `Cache-Control: no-store`, and drives the "Rule-Based Macroeconomic
Context" panel with the required attribution. See **`FRED_COMPLIANCE.md`**.

Phase 3 Silver products added: `silver_bea_value`, `silver_short_sale_volume`
/ `silver_short_interest` (separate datasets; a corrected FINRA file is kept
alongside the original via `file_version`), and `silver_security_master`
(keyed on a stable internal security id, not the reusable ticker). BLS
observations reuse `silver_macro_observation`, preserving BLS-specific fields
(period code, footnotes, preliminary flag) in a `source_fields` column.

## Reference-calculation adapter

Every Gold indicator with a standard, named definition is cross-checked
against a second, independently-coded implementation -
`catalystiq/validation/reference/`:

- **TA-Lib** (pinned `TA-Lib==0.7.1`) for SMA, RSI, MACD, ATR, OBV,
  Bollinger Bands, the Accumulation/Distribution line, and MFI.
- **TradingView's published formula**, independently recoded, for
  standard indicators TA-Lib doesn't carry: Relative Volume, Chaikin
  Money Flow, Price Volume Trend, Historical Volatility, and pivot/
  fractal swing points.
- An **independent financial-statistics implementation** (numpy/scipy) for
  Beta, Sharpe/Sortino/Calmar, and historical/parametric VaR.
- Composite, decision-rule outputs (market regime, trend structure,
  breakout state, liquidity classification) have no single universal
  external reference value - they're validated instead via documented
  decision rules + synthetic scenarios
  (`catalystiq/validation/reference/composite_scenarios.py`).

This never runs in the synchronous request path. It runs in CI
(`.github/workflows/reference_validation.yml`, triggered on any change to
an indicator implementation or its configuration), asynchronously in
production on a configurable sample of completed Gold builds plus any run
a cheap synchronous anomaly check flags
(`REFERENCE_VALIDATION_SAMPLE_RATE`/`REFERENCE_VALIDATION_INTERVAL_SECONDS`,
`catalystiq/validation/reference/scheduler.py` - same in-process-loop
pattern as the order scheduler, no task queue), and on demand via the
CI workflow's manual `workflow_dispatch` trigger - run that before bumping
any product's `calculation_version` constant, since this repo has no other
release-promotion gate. A mismatch never overwrites the Gold output - it's
quarantined (`data_quality_status="quarantined"`, excluded from cache
reuse) with a full audit row (`gold_reference_check`: symbol, silver
build, calculation/configuration version, reference library + version,
parameters, expected/actual values, tolerance, and the discrepancy
reason).

## A note on this build environment

Yahoo Finance's and Webull's hosts are both blocked by this sandbox's egress
policy, so `YahooFinanceProvider` and `WebullBroker` were both validated with
mocked responses rather than a live call. Both should work unmodified
against the real services once deployed somewhere with normal internet
egress.

Separately (unrelated to the network block): installing
`webull-openapi-python-sdk` in this sandbox failed on its `paho-mqtt`
dependency (`paho-mqtt==1.6.1` has no prebuilt wheel, and building it from
source hit a `setuptools`/`distutils` incompatibility specific to this
Debian-based image). Since the trade/order REST client doesn't import
`paho-mqtt` at all (only the separate market-data streaming module does),
installing the SDK with `pip install --no-deps webull-openapi-python-sdk`
plus its other dependencies (everything in its `requires.txt` except
`paho-mqtt`) is enough for `WebullBroker` to work. If your deployment
environment doesn't hit this same `paho-mqtt` build issue, a plain
`pip install -r requirements.txt` should just work.
