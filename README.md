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
- `BrokerProvider` interface (`catalystiq/providers/broker.py`), with an
  Alpaca paper-trading implementation carried over from the original
  `app.py`. The build spec's execution zone targets Webull's paper API;
  Alpaca is the working paper-trading integration this repo already had,
  wired up behind the same interface so it can be swapped later without
  touching routers or callers.
- Postgres schema (`catalystiq/db/models.py`, migrated with Alembic) matching
  the spec's schema sketch: `tickers`, `price_history`,
  `indicator_snapshots`, `options_snapshots`, `news_events`,
  `behavioral_events`, `reinforcement_stats`, `reports`.
- The Data Validation Layer (`catalystiq/validation/data_quality.py`):
  chronological-order check, dedupe, missing-trading-day detection, abnormal
  price-gap flagging (z-score), and a live-quote cross-check — run before
  any bar is persisted as "ready for rules."
- An ingestion endpoint (`POST /market-data/ingest/{symbol}`) that pulls
  OHLCV, runs the validation layer, and upserts cleaned bars into
  `price_history`, returning the resulting data-quality report.

There's also an initial slice of **Phase 7 — Frontend** (`frontend/`): a
React + Vite + Tailwind dashboard. The watchlist grid shows hand-authored
demo `AnalysisCard`s (rating/probability/confidence/behavioral signal) built
to the spec's §10 component contract — clearly labeled as demo data. The
header's ticker search is wired to the *real* backend: it calls the quote
and ingest endpoints and shows actual price + data-quality results, with no
fabricated rating, since the scoring/behavioral engines don't exist yet.

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
    market_data.py       # MarketDataProvider ABC + YahooFinanceProvider
    broker.py              # BrokerProvider ABC + AlpacaPaperBroker
  schemas/                # Pydantic request/response/domain shapes
  validation/
    data_quality.py        # Data Validation Layer (§2.9)
  routers/
    broker.py               # /paper/* (account, positions, orders)
    market_data.py            # /market-data/* (quote, ohlcv, ingest, ...)
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
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | Alpaca paper-trading credentials | — (required for `/paper/*`) |
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

## A note on this build environment

Yahoo Finance's hosts are blocked by this sandbox's egress policy, so the
`YahooFinanceProvider` was validated with mocked responses rather than a live
call. It should work unmodified against real Yahoo Finance data once
deployed somewhere with normal internet egress — the `yfinance` calls it
wraps are standard, unauthenticated public endpoints.
