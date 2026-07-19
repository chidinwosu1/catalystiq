# Point-in-Time Provenance Contract

A single, reusable definition of the point-in-time facts every persisted
record carries, so providers don't each redefine them. Lives in
`catalystiq/provenance/` and is **reconciled with the merged ML feature
manifest** (`catalystiq/ml/features/schema.py`, PR #13).

> The ML feature manifest is now merged. This contract has been aligned to it:
> the `data_quality_status` vocabulary is now **value-identical** to the ML
> feature enum, and a cross-check test (`test_quality_enum_matches_ml_feature_contract`)
> fails if the two ever drift. Where the manifest and this contract differed, the
> differences were **reported, not silently resolved** — see *Reconciliation
> with the ML manifest* below.

## The six fields

Five are **persisted source facts**; `freshness` is **always computed
dynamically** (never stored — a record marked "current" today would be wrong
tomorrow).

| Field | Persisted? | Meaning |
|---|---|---|
| `source_provider` | yes | Canonical provider id: `yahoo`, `sec_edgar`, `bls`, `bea`, `finra`, `nasdaq_trader`, `twelve_data`, `webull`, `nyse`, `fred_restricted` |
| `source_event_timestamp` | yes | When the underlying observation / filing / candle / event occurred |
| `available_at_timestamp` | yes | Earliest time we could legally + technically have known the value |
| `retrieved_at_timestamp` | yes | When Catalyst IQ actually retrieved it |
| `data_quality_status` | yes | Canonical ML enum: `ok \| stale \| imputed \| missing \| invalid` |
| `freshness` | **computed** | `current \| stale \| future_dated \| unknown` |

Optional source identity, recorded **only where a real value exists** (never
invented): `source_dataset`, `source_series_id`, `source_record_id`,
`source_url`, `license_policy_id`.

All timestamps are timezone-aware UTC; naive datetimes from storage are assumed
UTC.

## Rules (enforced in `catalystiq/provenance/contract.py`)

- **Temporal ordering:** `source_event ≤ available_at ≤ retrieved_at` where each
  is present (`validate_temporal_ordering`). A corrected/backfilled record may
  legitimately have `source_event` after `available_at` — pass
  `is_correction=True` to document that exception.
- **Dynamic freshness** (`compute_freshness`): `future_dated` if the event/
  availability is after now; `unknown` if there are no timestamps; otherwise
  `stale`/`current` from the NYSE session policy (daily data) or a per-frequency
  max age (weekly/monthly/quarterly/annual).
- **ML lookahead guard** (`assert_point_in_time_safe`): a feature is rejected
  when `available_at_timestamp > prediction_timestamp` — or when availability is
  unknown. An ML feature MUST pass this before being used at a prediction time.
  This is the reusable rule the future ML pipelines call; **no model code lives
  here.**

## Reconciliation with the ML manifest

When the ML manifest merged, this contract was mapped onto it and the following
differences were surfaced. Only the enum was changed (per an explicit decision
to make the ML enum canonical); the rest are documented design choices, not
silent contract edits.

| Area | ML manifest | This contract before | Resolution |
|---|---|---|---|
| Quality vocabulary | `ok \| stale \| imputed \| missing \| invalid` | `valid \| warning \| invalid \| insufficient \| quarantined` | **ML made canonical.** Provenance enum re-valued to match; legacy `validation_status` **retained** for auditability; warning reasons kept in `data_quality_warnings`. Unknown legacy statuses **fail closed to `invalid`**. |
| Freshness | not a manifest field | computed, never persisted | Kept provenance-side only; it is derived state, not an ML input fact. |
| Identity columns | optional feature identity | absent from the mixin | **Added** `source_dataset`, `source_series_id`, `source_url`, `license_policy_id` to `SilverRecordMixin` (provenance completeness). |
| Provider licensing | `PROVIDER_LICENSE` map keyed by canonical id | provider stored as a class name on the price path | Provider now **canonicalized on write** for the price path and on read everywhere; `license_policy_id` recorded where known. |
| Flow direction | features pull from storage | contract projected over storage | Unchanged; the projection layer (`provenance/projection.py`) is the seam the ML feature provider reads through. |

## Reconciliation with existing storage (no duplication)

The contract maps onto columns that already exist rather than redefining them:

| Contract field | Existing column | Notes |
|---|---|---|
| `source_provider` | `SilverRecordMixin.provider` | Canonicalized (`YahooFinanceProvider` → `yahoo`) on both read and, for the price path, on write |
| `source_event_timestamp` | `effective_at` / domain date | Callers pass the real event date (observation_date, session_date, trade_date, filing_date, bar date, …) |
| `available_at_timestamp` | `source_available_at` | **Now populated** by the pipelines and backfilled by migration; falls back to `retrieved_at` only where still null |
| `retrieved_at_timestamp` | `retrieved_at` | Direct |
| `data_quality_status` | canonical ML column + legacy `validation_status` | `clean`/`clean_with_warnings`→`ok`, `insufficient_data`→`missing`, `imputed`→`imputed`, `stale`→`stale`, `quarantined`/`rejected`/unknown→`invalid` |

`SilverPriceBar` has no provider columns of its own, so it is projected via its
Bronze ingestion run (`provenance_from_bronze_run`): provider + timestamps from
the run, event date from the bar, and — preferentially — the bar's own
`source_available_at` point-in-time floor when present.

Point-in-time handling already in the system is **preserved**: macro vintages
(`realtime_start/end`), SEC amendment vintages (`accession_number` +
`filing_date`), FINRA `file_version`, and scheduled-vs-actual release dates.

## The schema migration (`a1b2c3d4e5f6`)

Backward-compatible and additive. It:

1. Adds the canonical `data_quality_status` (ML enum) to every Silver record
   **alongside** the retained legacy `validation_status`, and backfills it from
   the legacy value (unknown → `invalid`, fail closed).
2. Adds the optional identity columns (`source_dataset`, `source_series_id`,
   `source_url`, `license_policy_id`) to the shared mixin, consolidating the
   pre-existing `source_url` on the two tables that already had it.
3. Adds `source_available_at` to `SilverPriceBar` (the one Silver table without
   the mixin) and backfills a **point-in-time floor = end-of-day of the bar
   date** — never claiming a daily bar was knowable before its session closed.
4. Populates `source_available_at` on the mixin tables (= `retrieved_at`, a safe
   floor) where it was null.
5. Canonicalizes the market-price provider recorded on `bronze_ingestion_run`
   (`YahooFinanceProvider` → `yahoo`).

`downgrade()` drops the added columns; the data-only backfills are safe,
canonical values and are not reversed.

### Point-in-time price availability

A daily bar for date *D* is knowable only **after** *D*'s session closes, so its
`source_available_at` is set to end-of-day *D* (a calendar-free, conservative
floor). This is what prevents look-ahead: an "as-of" query filtering
`source_available_at <= prediction_timestamp` returns only bars that had already
closed, and the lookahead guard rejects any intraday-same-day use. Proven by
`tests/test_provenance_migration.py`
(`test_point_in_time_query_returns_only_knowable_bars`,
`test_price_bar_available_at_prevents_lookahead`).

## FRED stays ephemeral

The restricted FRED integration (`catalystiq/fred/`) writes nothing to storage
and is **excluded from all persisted provenance and ML feature pipelines**. The
provenance package imports neither `catalystiq.db` nor `catalystiq.fred` (proved
by test). The migration touches no FRED data.

## Status

**Implemented:** the reusable contract, canonicalization, the ML-aligned quality
enum, dynamic freshness, temporal-ordering validation, the lookahead guard,
projection over existing records, the additive schema migration with backfill,
and the pipeline population of `source_available_at`. Fully tested in
`tests/test_provenance.py` (contract) and `tests/test_provenance_migration.py`
(migration backfill + point-in-time retrieval / no leakage).

**Not done here (out of scope):** no ML model code, no training, no feature
materialization. This layer is the point-in-time foundation those will read
through; it does not build them.
