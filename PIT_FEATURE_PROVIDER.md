# Point-in-Time Feature Provider

`catalystiq/features/pit_provider.py` — `SilverPitFeatureProvider` — is the
**concrete** implementation of the ML foundation's provider-neutral
`PointInTimeFeatureProvider` Protocol (`catalystiq/ml/features/provider.py`). It
is the "point-in-time read adapter" the feature manifest says the price-derived
groups need, wired against the provenance schema from the provenance migration.

## Separation of concerns

- Lives in `catalystiq/features/`, **not** `catalystiq/ml/`. The ML package
  stays integration-free and consumes only the abstract Protocol.
- Imports the ML **schema** (the contract — `PointInTimeFeature`,
  `DataQualityStatus`, `FEATURE_CATALOG`) and the `Bar` label type, but **no ML
  model code**, and does not modify the ML contract or the manifest.
- Reads the database and the existing analysis engine
  (`compute_technical_snapshot`); it does not call any external provider.

## Point-in-time guarantees

1. **Availability filter.** `get_features(symbol, prediction_timestamp)` reads
   only `SilverPriceBar` rows whose `source_available_at <= prediction_timestamp`.
   A bar with `source_available_at IS NULL` is **excluded** — we can't prove it
   was knowable (fail closed). A daily bar's availability floor is end-of-day of
   the bar date (set by the pipeline / provenance migration), so same-day
   intraday predictions never see that day's bar.
2. **Provenance stamping.** Every returned feature carries `source_provider`,
   `source_event_timestamp`, `available_at_timestamp`, `retrieved_at_timestamp`
   and `data_quality_status`, drawn from the newest *visible* bar. The ML
   schema's own leakage gate (`available_at <= prediction_timestamp`,
   `source_event <= available_at`) therefore passes as defense in depth — proven
   by `validate_feature(...) is None` for every emitted feature in the tests.
3. **No fabrication.** Inputs with no wired point-in-time source are emitted with
   `data_quality_status = MISSING` and `feature_value = None`, so the dataset
   builder records them as `requirement_gaps` rather than training on invented
   values.

## What is computed vs. recorded-missing

**Computed** from the symbol's own point-in-time bars (and a benchmark for the
cross-asset ones):

- Price/OHLCV: `adj_open/high/low/close`, `log_return_1d/5d/20d`
- Trend: `sma_20/50/200`, `price_vs_sma_50`, `sma_50_slope`
- Momentum: `momentum_20d/60d`
- Oscillators: `rsi_14`, `macd`, `macd_signal`, `macd_hist`
- Volatility: `atr_14`, `realized_vol_20d`
- Volume/liquidity: `relative_volume_20d`, `dollar_volume_20d`, `adv_dollar_20d`
- Gaps: `overnight_gap_pct`
- Market/relative (vs benchmark, default `SPY`): `market_return_20d`,
  `relative_strength_60d`, `beta_60d`
- Data quality: `feature_freshness_days`, `feature_completeness`

**Recorded MISSING** (no wired point-in-time source yet — honest gaps, not
zeros): `estimated_spread_bps`, `dist_to_support/resistance_pct`,
`sector_return_20d`, `market_regime`, `trading_days_to_earnings`,
SEC fundamentals (`pit_revenue_yoy`, `pit_gross_margin`, `recent_filing_event`),
macro (`macro_cpi_yoy_pit`, `macro_gdp_qoq_pit`), and the rule-based score
features. FRED is never sourced (hard-blocked in the ML schema regardless).

## Label support (offline only)

`get_executable_entry` returns the **next** session's open after the prediction
(forward-looking, used only for offline label generation), and
`get_forward_path` returns the forward OHLC bars across the horizon. These are
intentionally *not* availability-filtered against the prediction timestamp —
they generate outcomes, never inference features.

## Flagged: manifest status not changed here

Wiring this adapter would let the feature manifest move the price-derived groups
from `integration_exists_not_point_in_time` to `wired`. That mapping
(`_GROUP_STATUS` in `catalystiq/ml/features/manifest.py`) is **ML-contract-owned**,
so it is **not** changed in this PR. Flagging it for review rather than editing
it silently, per the standing rule. Fundamentals/macro/earnings/regime/rule-based
remain not-wired until their own point-in-time reads land.

## Not in scope

No ML model code, no training, no feature materialization/caching, no
serving-time wiring. This layer is the point-in-time read those later,
separately-approved phases will consume.
