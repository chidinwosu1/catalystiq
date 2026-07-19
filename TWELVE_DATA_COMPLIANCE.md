# Twelve Data Integration — Restricted Personal-Use Policy & Controls

Catalyst IQ uses **Twelve Data** strictly as an **optional, restricted,
personal-use** secondary market-data source for private cross-provider
validation. This document records the policy and where each control lives.

**Plan assumed:** Basic (8 credits/minute, 800 credits/day). Update the limits
in config if your plan differs — never above what your plan permits.

---

## Permitted use (personal / non-commercial)

Twelve Data is used only for private, personal, internal, non-commercial use —
validating Yahoo (primary) quotes. It is **not** used in a public or multi-user
version of Catalyst IQ, and raw Twelve Data information is **not** redistributed
or provided to other users. Keep this a single-user deployment while enabled.

## Central plan-limit enforcement (credits)

All Twelve Data requests route through one process-central gate
(`catalystiq/providers/twelve_data_gate.py`), which enforces:

- **8 credits/minute** and **800 credits/day** (`twelve_data_credits_per_minute`
  / `twelve_data_credits_per_day`, Basic-plan defaults).
- **Per-endpoint credit weights** (`CREDIT_WEIGHTS`) — a request is charged in
  *credits*, not request-count, because some endpoints/batches cost more than
  one credit.
- An optional local `twelve_data_daily_request_budget` that can only **lower**
  the daily cap.

The gate is in-process. For the single-instance deployment that is the whole
app; **do not run multiple workers with Twelve Data enabled** without moving the
counter to a shared store (each worker would keep its own budget).

*Enforced/tested by:* `tests/test_twelve_data_compliance.py::test_per_minute_credit_limit_and_reset`,
`::test_endpoint_credit_weights_are_tracked`,
`::test_daily_cap_disables_then_clears_on_day_rollover`.

## Automatic shut-off (fail closed)

The provider auto-disables (subsequent calls fail closed) when:

- the **daily credit cap** is hit — disabled until the UTC day rolls over;
- **credential validation fails** (HTTP 401/403, or an "invalid API key" error
  body);
- a **licensing/plan restriction** is returned (professional/exchange/
  redistribution/"upgrade your plan").

A benign error (e.g. "symbol not found") does **not** disable the provider.

*Tested by:* `::test_auth_failure_auto_disables_provider`,
`::test_licensing_error_body_auto_disables`,
`::test_invalid_key_body_auto_disables`,
`::test_benign_error_body_does_not_disable`.

## No permanent storage of raw data / no reconstruction

Raw Twelve Data market data is **not** permanently stored (retention under the
selected plan is not yet confirmed). The only place TD values previously reached
the database — the cross-provider comparison record — now stores, for a
restricted provider, **only the tolerance outcome and provenance**:

- **Not stored:** the raw TD value, its timestamp, the absolute/relative
  difference, or any numeric difference in the reason string.
- **Stored:** `secondary_provider` (provenance), `within_tolerance` (a boolean
  outcome), and the primary (Yahoo) value.

Because only a within/outside-tolerance boolean is kept (a *range*, not a
number), the original TD price **cannot be reconstructed** from any saved value.
Twelve Data OHLCV is never written to Bronze or Silver (no code path persists
it). Temporary in-memory calculations and private internal analysis are
permitted; the tolerance check is computed in memory and discarded.

*Enforced by:* `catalystiq/pipelines/comparison.py` (`RESTRICTED_NO_RAW_PERSIST`
branch); *tested by:*
`tests/test_twelve_data_compliance.py::test_restricted_secondary_persists_no_raw_value`.

## No model training / backtesting

Twelve Data information is not used for model training, fine-tuning, or permanent
historical backtesting (not permitted until written confirmation under the
plan). TD is isolated from the analysis/scoring/validation/order code — none of
those modules import it, so it cannot feed a model or a backtest.

*Tested by:* `::test_analysis_and_order_modules_do_not_import_twelve_data`.

## Provenance & no branding

Raw values and any Twelve Data-derived analysis are identified by provenance
(the provider name is recorded on the comparison record and on the primary
value's `primary_provider`). Twelve Data branding is **not** displayed and no
endorsement is implied — the source appears only as the internal identifier
`twelve_data` in health/admin views.

## Security (key handling)

`TWELVE_DATA_API_KEY` is backend-only. It is never exposed in the frontend,
browser responses, URLs, telemetry, logs, or source control: the transport
redacts the `apikey` parameter from logs, and the frontend never references the
key.

*Tested by:* `::test_api_key_is_redacted_in_logs`,
`::test_frontend_never_references_the_twelve_data_key`.

## Optional (kill switch)

Disabled by default (`ENABLE_TWELVE_DATA=false`). When disabled or removed,
Catalyst IQ works unchanged — the compare endpoint refuses cleanly and Yahoo
remains the sole market-data source.

*Tested by:* `::test_disabled_twelve_data_is_optional`.

## Do-not-change without approval

Do not enable a public/multi-user deployment, raise the credit limits above the
plan, enable permanent storage/backtesting/model use, or use data requiring
professional/exchange/redistribution licensing, without explicit owner approval
and (where required) written confirmation from Twelve Data.
