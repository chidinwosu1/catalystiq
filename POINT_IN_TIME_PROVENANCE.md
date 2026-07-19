# Point-in-Time Provenance Contract

A single, reusable definition of the point-in-time facts every persisted
record carries, so providers don't each redefine them. Lives in
`catalystiq/provenance/` and is designed to map onto the ML feature manifest
once that is finalized on the ML branch.

> There is no canonical ML feature manifest yet — the ML branch is creating it.
> This is the **minimum shared standard** implemented now. When the manifest
> merges, we map these records to it and **report any incompatible field
> definitions rather than silently changing this contract.**

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
| `data_quality_status` | yes | Enum: `valid \| warning \| invalid \| insufficient \| quarantined` |
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

## Reconciliation with existing storage (no duplication)

The contract maps onto columns that already exist rather than redefining them:

| Contract field | Existing column | Notes |
|---|---|---|
| `source_provider` | `SilverRecordMixin.provider` | Canonicalized on read (`YahooFinanceProvider` → `yahoo`) |
| `source_event_timestamp` | `effective_at` / domain date | Callers pass the real event date (observation_date, session_date, trade_date, filing_date, bar date, …) |
| `available_at_timestamp` | `source_available_at` | Declared but currently unpopulated → falls back to `retrieved_at` (we could not have known a value before retrieving it) |
| `retrieved_at_timestamp` | `retrieved_at` | Direct |
| `data_quality_status` | `validation_status` | `clean`→`valid`, `clean_with_warnings`→`warning`, `quarantined`→`quarantined`, `insufficient_data`→`insufficient`; unknown→`warning` (never silently `valid`) |

`SilverPriceBar` has no provider columns of its own, so it is projected via its
Bronze ingestion run (`provenance_from_bronze_run`): provider + timestamps from
the run, event date + quality from the bar.

Point-in-time handling already in the system is **preserved**: macro vintages
(`realtime_start/end`), SEC amendment vintages (`accession_number` +
`filing_date`), FINRA `file_version`, and scheduled-vs-actual release dates.

## FRED stays ephemeral

The restricted FRED integration (`catalystiq/fred/`) writes nothing to storage
and is **excluded from all persisted provenance and ML feature pipelines**. The
provenance package imports neither `catalystiq.db` nor `catalystiq.fred` (proved
by test).

## Status / next step (flagged)

**Implemented now:** the reusable contract, canonicalization, quality mapping,
dynamic freshness, temporal-ordering validation, the lookahead guard, and
projection over existing records — with full unit tests
(`tests/test_provenance.py`). This adds **no** schema migration: the five facts
are already persisted in `SilverRecordMixin`, and the contract standardizes,
computes, and validates on top of them.

**Proposed follow-up (needs a schema decision — flagged, not done):**
1. Populate `source_available_at` in the ingestion pipelines (today always
   null → the contract conservatively falls back to `retrieved_at`).
2. Persist canonical `source_provider` on the market-price path (it stores a
   class name today) and add provenance columns to `SilverPriceBar`.
3. Add the optional identity columns (`source_dataset`, `source_url`,
   `license_policy_id`) to the shared mixin.

These touch ~12 Silver tables, so they should be reviewed against the ML
feature manifest's field definitions before the migration is written — hence
they are proposed here rather than applied silently.
