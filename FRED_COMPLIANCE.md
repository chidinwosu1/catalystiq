# FRED Integration — Compliance Policy & Controls

Catalyst IQ uses the **FRED® API** (Federal Reserve Bank of St. Louis) to show a
small, fixed set of public-domain macroeconomic indicators as an **ephemeral,
rule-based context panel** only. This document records the policy, the reviewed
terms, the approved series, and where each control lives in the code.

> **This product uses the FRED® API but is not endorsed or certified by the
> Federal Reserve Bank of St. Louis.**

**Terms reviewed:** <https://fred.stlouisfed.org/docs/api/terms_of_use.html>
**Date reviewed:** 2026-07-19
(FRED may change its terms; re-review before expanding use — see §10.)

---

## Permitted purpose (req. #1)

FRED is used **only** to retrieve the allowlisted indicators below, for private,
personal, non-commercial investment research, presented as **informational
market context**. It is never presented as investment advice or as guaranteed
accurate, complete, or current. The disclaimer travels with every response
(`catalystiq/fred/service.py:DISCLAIMER`) and is shown on the panel.

## No AI/ML use (req. #2)

FRED data is **never** used to train, fine-tune, test, validate, evaluate,
backtest, or develop any model, and is never sent to an LLM, embedding model,
agent, prompt, vector store, or feature store. It never feeds a confidence,
probability-of-profit, expected-return, ranking, or recommendation score. It
appears **only** in the separately labeled "Rule-Based Macroeconomic Context"
panel, which reports values as-is and computes no score.

*Enforced by:* import-graph isolation (both directions) — see §Isolation and
`tests/test_fred_compliance.py::test_fred_package_does_not_import_persistence_or_ml`
and `::test_scoring_and_order_modules_do_not_import_fred`.

## No permanent storage (req. #3)

FRED data and API responses are **never** written to Bronze, Silver, Gold, or
any application/analytics/feature/training/historical store; never archived,
persisted, versioned, or permanently cached; and never written to logs,
monitoring, error trackers, files, backups, or telemetry. Responses are
processed in memory and discarded after the request.

- The FRED reader takes **no database session** and holds **no cache**
  (`catalystiq/fred/provider.py`, `catalystiq/fred/service.py`).
- The FRED path was removed from the medallion pipeline
  (`catalystiq/pipelines/macro_pipeline.py`, `catalystiq/routers/macro.py`).
- Every FRED response is served **`Cache-Control: no-store`**
  (`catalystiq/routers/fred.py`).
- The shared HTTP transport never logs response bodies and redacts secrets
  (`catalystiq/providers/transport.py`).
- **No persistent cache is implemented.** If temporary technical caching ever
  becomes unavoidable, stop and get written clarification from FRED first (§10).

*Enforced by:* `tests/test_fred_compliance.py::test_context_endpoint_persists_nothing`,
`::test_build_context_takes_no_db_session`,
`::test_context_endpoint_sets_no_store`,
`::test_no_observation_value_is_logged`.

## Official API only (req. #4)

Data is accessed solely through the documented FRED REST API over the shared
transport (timeouts, bounded retries, rate limiting, circuit breaker). No
scraping, bulk download, robots, or reproduction of the FRED experience. A
bounded look-back keeps each request small. Outages/unavailable series/rate
limits are handled gracefully — a single failing series is marked
`unavailable` and the rest of the panel still renders.

## Series allowlist (req. #5)

Retrieval is gated by a manually-controlled allowlist
(`catalystiq/fred/allowlist.py`). Only series that are **listed** *and*
classified **PUBLIC_DOMAIN** can be fetched; unknown ids and any
`COPYRIGHTED_PREAPPROVAL` series are hard-blocked before any network call
(`require_retrievable`). Adding or promoting a series requires a fresh review
and explicit approval (§10).

### Approved (public-domain) series

| Series | Title | Original owner | Attribution shown | Units | Freq |
|---|---|---|---|---|---|
| `DGS10` | 10-Year Treasury Constant Maturity Rate | Board of Governors of the Federal Reserve System (US) | Source: Board of Governors of the Federal Reserve System (US) via FRED | Percent | Daily |
| `DGS2` | 2-Year Treasury Constant Maturity Rate | Board of Governors of the Federal Reserve System (US) | Source: Board of Governors of the Federal Reserve System (US) via FRED | Percent | Daily |
| `T10Y2Y` | 10-Year minus 2-Year Treasury | Federal Reserve Bank of St. Louis | Source: Federal Reserve Bank of St. Louis via FRED | Percent | Daily |
| `FEDFUNDS` | Effective Federal Funds Rate | Board of Governors of the Federal Reserve System (US) | Source: Board of Governors of the Federal Reserve System (US) via FRED | Percent | Monthly |
| `UNRATE` | Unemployment Rate | U.S. Bureau of Labor Statistics | Source: U.S. Bureau of Labor Statistics via FRED | Percent | Monthly |
| `CPIAUCSL` | CPI-U: All Items (SA) | U.S. Bureau of Labor Statistics | Source: U.S. Bureau of Labor Statistics via FRED | Index 1982-84=100 | Monthly |
| `GDPC1` | Real Gross Domestic Product | U.S. Bureau of Economic Analysis | Source: U.S. Bureau of Economic Analysis via FRED | Chained 2017 $B | Quarterly |

**Purpose (all):** informational macro backdrop shown in the Rule-Based
Macroeconomic Context panel on the **Data Sources** page. Not stored, not used
for any score/model/order.

### Blocked (copyrighted — pre-approval required)

| Series | Title | Owner | Status |
|---|---|---|---|
| `VIXCLS` | CBOE Volatility Index: VIX | Chicago Board Options Exchange (CBOE) | **BLOCKED** — copyrighted; pre-approval required |
| `SP500` | S&P 500 | S&P Dow Jones Indices LLC | **BLOCKED** — copyrighted; pre-approval required |

These are listed **only** to document the block; they are never fetched or
displayed. *Enforced by:*
`tests/test_fred_compliance.py::test_copyrighted_series_are_hard_blocked`.

## Attribution (req. #6)

Every indicator displays its original-owner attribution ("Source: … via FRED"),
and the required notice —
*"This product uses the FRED® API but is not endorsed or certified by the
Federal Reserve Bank of St. Louis."* — is shown prominently on the panel and on
the **Data Sources** page (`frontend/src/components/RuleBasedMacroContext.tsx`).
Copyright/ownership/source/attribution notices are never removed or obscured,
and the Federal Reserve Bank's logo is not used, nor is any sponsorship,
affiliation, certification, or endorsement implied.

## Security (req. #7)

`FRED_API_KEY` is a **backend-only** environment variable. It is never exposed
in frontend code, browser responses, URLs shown to users, source control, logs,
screenshots, or error messages. All FRED requests are routed through the
authenticated backend (`/fred/*` requires auth); brokerage credentials,
holdings, trades, and personal information are never included in FRED requests.

*Enforced by:* `tests/test_fred_compliance.py::test_frontend_never_references_the_fred_key`
(the built bundle is also verified to contain no key), transport secret
redaction, and the backend-only settings design.

## Separation & kill switch (req. #8)

The integration is an isolated module (`catalystiq/fred/`) that cannot feed the
ML/AI, scoring, backtesting, or order-execution systems. Default configuration
is **`ENABLE_FRED=false`**. When FRED is disabled, unavailable, or removed, the
panel reports `available: false` and the rest of Catalyst IQ works unchanged.
FRED never triggers, recommends, sizes, schedules, or submits an order.

## Isolation (import graph)

`catalystiq/fred/` imports **none** of: `catalystiq.db`, `catalystiq.pipelines`,
`catalystiq.analysis`, `catalystiq.orders`, `catalystiq.scheduler`,
`catalystiq.validation`. Conversely, none of the analysis/validation/orders/
scheduler modules import `catalystiq.fred`. Both directions are asserted by AST
import-graph tests in `tests/test_fred_compliance.py`.

## Tests (req. #9)

`tests/test_fred_compliance.py` proves FRED data cannot enter databases, AI/ML
pipelines, prompts, logs, trading scores, backtests, or order execution;
`tests/test_fred_provider.py` covers offline adapter parsing. A repository check
asserts the key never appears in the frontend. **Do not enable the integration
until these pass and this document has been reviewed.**

## Future changes (req. #10)

Do not expand FRED's use, add series, introduce caching/storage, connect it to
AI/ML, or make Catalyst IQ available to other users without a **new terms
review and explicit owner approval**. Record the date and URL of the terms
reviewed at the top of this file each time.
