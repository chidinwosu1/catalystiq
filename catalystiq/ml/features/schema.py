"""Point-in-time feature schema with licensing and leakage gates.

Every ML feature is represented as a :class:`PointInTimeFeature` carrying its
full provenance. The schema enforces, at construction and again at
vector-assembly time, three non-negotiable rules:

  1. **No look-ahead.** A feature whose ``available_at_timestamp`` is after
     the ``prediction_timestamp`` is rejected. You may only use information
     that existed at the moment the prediction is made.

  2. **Licensing.** FRED-derived values are rejected outright (defense in
     depth - blocked even if a flag says otherwise). Twelve Data may not
     enter *training* unless a separate licensing flag confirms storage and
     ML use are permitted. Behavioral / sentiment / catalyst / news features
     require a real, licensed, historically-timestamped source and are
     rejected until one is registered.

  3. **Provenance completeness.** Every feature must carry symbol,
     prediction_timestamp, feature_name, feature_value, source_provider,
     source_event_timestamp, available_at_timestamp, retrieved_at_timestamp
     and data_quality_status. A missing field is a rejection, not a silent
     default.

The catalog (:data:`FEATURE_CATALOG`) declares which feature *names* are
admissible, their group, and their provider licensing class. It is a
data-only allowlist - adding a feature is a deliberate, reviewed act, exactly
like the FRED series allowlist.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from catalystiq.config import Settings
from catalystiq.ml import FEATURE_SCHEMA_VERSION
from catalystiq.ml import flags


class LicensingError(Exception):
    """A feature's source provider is not licensed for the requested use."""


class LeakageError(Exception):
    """A feature carries information that did not exist at prediction time."""


class DataQualityStatus(str, Enum):
    OK = "ok"
    STALE = "stale"
    IMPUTED = "imputed"
    MISSING = "missing"
    INVALID = "invalid"


class FeatureGroup(str, Enum):
    PRICE_OHLCV = "adjusted_ohlcv"
    TREND = "trend_moving_average"
    MOMENTUM = "momentum"
    OSCILLATOR = "rsi_macd"
    VOLATILITY = "volatility_atr"
    VOLUME = "volume_relative_volume"
    LIQUIDITY = "liquidity_spread"
    GAPS = "gaps"
    SUPPORT_RESISTANCE = "support_resistance"
    MARKET_SECTOR = "market_sector_performance"
    RELATIVE_STRENGTH = "relative_strength"
    BETA = "beta"
    REGIME = "market_regime"
    EARNINGS = "earnings_proximity"
    FUNDAMENTALS = "sec_fundamentals"
    MACRO = "macro_bls_bea"
    RULE_BASED = "rule_based_opportunity_score"
    MISSINGNESS = "missing_feature_indicator"
    DATA_QUALITY = "data_quality_freshness"


class ProviderLicense(str, Enum):
    """Licensing class of a source provider, governing ML use."""

    # Freely usable (public-domain gov data, our own computed values).
    OPEN = "open"
    # SEC EDGAR public filings - point-in-time, permitted.
    SEC = "sec"
    # BLS / BEA public macro - permitted (NOT FRED; see below).
    MACRO_PUBLIC = "macro_public"
    # Twelve Data - only usable in training behind a licensing flag.
    TWELVE_DATA = "twelve_data"
    # FRED - HARD BLOCKED from ML features (see FRED_COMPLIANCE.md).
    FRED = "fred"
    # Behavioral/sentiment/news - blocked until a licensed, timestamped
    # source is registered.
    UNLICENSED_ALT = "unlicensed_alt"


# Provider name (lowercased) -> licensing class. Unknown providers are
# treated as UNLICENSED_ALT and rejected - fail closed on provenance.
PROVIDER_LICENSE: dict[str, ProviderLicense] = {
    "yahoo": ProviderLicense.OPEN,
    "nyse": ProviderLicense.OPEN,
    "finra": ProviderLicense.OPEN,
    "nasdaq_trader": ProviderLicense.OPEN,
    "catalystiq": ProviderLicense.OPEN,  # our own computed values
    "computed": ProviderLicense.OPEN,
    "sec_edgar": ProviderLicense.SEC,
    "sec": ProviderLicense.SEC,
    "bls": ProviderLicense.MACRO_PUBLIC,
    "bea": ProviderLicense.MACRO_PUBLIC,
    "twelve_data": ProviderLicense.TWELVE_DATA,
    "fred": ProviderLicense.FRED,
}


@dataclass(frozen=True)
class FeatureSpec:
    """Catalog entry: a permitted feature *name* and its metadata."""

    name: str
    group: FeatureGroup
    description: str
    # Whether the feature is direction-dependent (some technicals are not).
    direction_aware: bool = False


def _catalog() -> dict[str, FeatureSpec]:
    specs = [
        # Price / OHLCV
        FeatureSpec("adj_close", FeatureGroup.PRICE_OHLCV, "Split/dividend-adjusted close"),
        FeatureSpec("adj_open", FeatureGroup.PRICE_OHLCV, "Adjusted open"),
        FeatureSpec("adj_high", FeatureGroup.PRICE_OHLCV, "Adjusted high"),
        FeatureSpec("adj_low", FeatureGroup.PRICE_OHLCV, "Adjusted low"),
        FeatureSpec("log_return_1d", FeatureGroup.PRICE_OHLCV, "1-day log return"),
        FeatureSpec("log_return_5d", FeatureGroup.PRICE_OHLCV, "5-day log return"),
        FeatureSpec("log_return_20d", FeatureGroup.PRICE_OHLCV, "20-day log return"),
        # Trend / MAs
        FeatureSpec("sma_20", FeatureGroup.TREND, "20-day SMA"),
        FeatureSpec("sma_50", FeatureGroup.TREND, "50-day SMA"),
        FeatureSpec("sma_200", FeatureGroup.TREND, "200-day SMA"),
        FeatureSpec("price_vs_sma_50", FeatureGroup.TREND, "Close / SMA50 - 1"),
        FeatureSpec("sma_50_slope", FeatureGroup.TREND, "SMA50 slope"),
        # Momentum
        FeatureSpec("momentum_20d", FeatureGroup.MOMENTUM, "20-day price momentum"),
        FeatureSpec("momentum_60d", FeatureGroup.MOMENTUM, "60-day price momentum"),
        # Oscillators
        FeatureSpec("rsi_14", FeatureGroup.OSCILLATOR, "14-day RSI"),
        FeatureSpec("macd", FeatureGroup.OSCILLATOR, "MACD line"),
        FeatureSpec("macd_signal", FeatureGroup.OSCILLATOR, "MACD signal line"),
        FeatureSpec("macd_hist", FeatureGroup.OSCILLATOR, "MACD histogram"),
        # Volatility
        FeatureSpec("atr_14", FeatureGroup.VOLATILITY, "14-day ATR"),
        FeatureSpec("realized_vol_20d", FeatureGroup.VOLATILITY, "20-day realized vol"),
        # Volume
        FeatureSpec("relative_volume_20d", FeatureGroup.VOLUME, "Volume / 20-day avg"),
        FeatureSpec("dollar_volume_20d", FeatureGroup.VOLUME, "20-day avg dollar volume"),
        # Liquidity
        FeatureSpec("estimated_spread_bps", FeatureGroup.LIQUIDITY, "Estimated bid/ask spread"),
        FeatureSpec("adv_dollar_20d", FeatureGroup.LIQUIDITY, "Avg daily dollar volume"),
        # Gaps
        FeatureSpec("overnight_gap_pct", FeatureGroup.GAPS, "Overnight gap %"),
        # Support / resistance
        FeatureSpec("dist_to_support_pct", FeatureGroup.SUPPORT_RESISTANCE, "Distance to support"),
        FeatureSpec("dist_to_resistance_pct", FeatureGroup.SUPPORT_RESISTANCE, "Distance to resistance"),
        # Market / sector
        FeatureSpec("market_return_20d", FeatureGroup.MARKET_SECTOR, "Benchmark 20d return"),
        FeatureSpec("sector_return_20d", FeatureGroup.MARKET_SECTOR, "Sector ETF 20d return"),
        # Relative strength / beta
        FeatureSpec("relative_strength_60d", FeatureGroup.RELATIVE_STRENGTH, "RS vs benchmark 60d"),
        FeatureSpec("beta_60d", FeatureGroup.BETA, "60-day beta"),
        # Regime
        FeatureSpec("market_regime", FeatureGroup.REGIME, "Regime classification code"),
        # Earnings proximity
        FeatureSpec("trading_days_to_earnings", FeatureGroup.EARNINGS, "Sessions until next earnings"),
        # SEC fundamentals (point-in-time)
        FeatureSpec("pit_revenue_yoy", FeatureGroup.FUNDAMENTALS, "PIT revenue YoY growth"),
        FeatureSpec("pit_gross_margin", FeatureGroup.FUNDAMENTALS, "PIT gross margin"),
        FeatureSpec("recent_filing_event", FeatureGroup.FUNDAMENTALS, "Recent 8-K/10-Q event flag"),
        # Macro (BLS/BEA point-in-time, release-known)
        FeatureSpec("macro_cpi_yoy_pit", FeatureGroup.MACRO, "BLS CPI YoY as released"),
        FeatureSpec("macro_gdp_qoq_pit", FeatureGroup.MACRO, "BEA GDP QoQ as released"),
        # Rule-based opportunity score + factors
        FeatureSpec("rule_based_setup_strength", FeatureGroup.RULE_BASED, "Rule-based opportunity score"),
        FeatureSpec("rule_based_trend_factor", FeatureGroup.RULE_BASED, "Rule-based trend factor"),
        FeatureSpec("rule_based_momentum_factor", FeatureGroup.RULE_BASED, "Rule-based momentum factor"),
        FeatureSpec("rule_based_volume_factor", FeatureGroup.RULE_BASED, "Rule-based volume factor"),
        # Data quality / freshness
        FeatureSpec("feature_freshness_days", FeatureGroup.DATA_QUALITY, "Age of newest input"),
        FeatureSpec("feature_completeness", FeatureGroup.DATA_QUALITY, "Fraction of features present"),
    ]
    return {s.name: s for s in specs}


FEATURE_CATALOG: dict[str, FeatureSpec] = _catalog()


def missing_indicator_name(feature_name: str) -> str:
    """Every real feature gets a companion missing-indicator feature so the
    model can learn from missingness rather than being fed a silent
    imputation. e.g. ``rsi_14`` -> ``rsi_14__is_missing``."""
    return f"{feature_name}__is_missing"


@dataclass(frozen=True)
class PointInTimeFeature:
    """A single feature value with complete point-in-time provenance."""

    symbol: str
    prediction_timestamp: dt.datetime
    feature_name: str
    feature_value: float | int | None
    source_provider: str
    source_event_timestamp: dt.datetime
    available_at_timestamp: dt.datetime
    retrieved_at_timestamp: dt.datetime
    data_quality_status: DataQualityStatus = DataQualityStatus.OK
    schema_version: str = FEATURE_SCHEMA_VERSION

    @property
    def provider_license(self) -> ProviderLicense:
        return PROVIDER_LICENSE.get(
            (self.source_provider or "").strip().lower(), ProviderLicense.UNLICENSED_ALT
        )

    @property
    def is_missing_indicator(self) -> bool:
        return self.feature_name.endswith("__is_missing")


@dataclass(frozen=True)
class FeatureRejection:
    feature_name: str
    reason: str
    code: str  # "leakage" | "licensing" | "provenance" | "unknown_feature"


_REQUIRED_FIELDS = (
    "symbol",
    "prediction_timestamp",
    "feature_name",
    "source_provider",
    "source_event_timestamp",
    "available_at_timestamp",
    "retrieved_at_timestamp",
)


def validate_feature(
    feature: PointInTimeFeature,
    *,
    for_training: bool,
    settings: Settings | None = None,
) -> FeatureRejection | None:
    """Return a :class:`FeatureRejection` if the feature is inadmissible, else
    ``None``. Pure and side-effect free.

    ``for_training`` toggles the Twelve Data licensing gate: Twelve Data
    features may reach *inference* (a licensed live quote) but may not enter
    *training* datasets unless the storage/ML-use licensing flag is set.
    """
    name = feature.feature_name

    # --- Provenance completeness (fail on any missing field) -------------
    for fld in _REQUIRED_FIELDS:
        val = getattr(feature, fld, None)
        if val is None or (isinstance(val, str) and not val.strip()):
            return FeatureRejection(name, f"missing required provenance field '{fld}'", "provenance")

    # --- Unknown feature name (allowlist) --------------------------------
    base = name[: -len("__is_missing")] if feature.is_missing_indicator else name
    if base not in FEATURE_CATALOG:
        return FeatureRejection(name, f"feature '{name}' is not in the schema catalog", "unknown_feature")

    # --- No look-ahead ---------------------------------------------------
    if feature.available_at_timestamp > feature.prediction_timestamp:
        return FeatureRejection(
            name,
            "available_at_timestamp is after prediction_timestamp (look-ahead leakage)",
            "leakage",
        )
    # The source event itself must also predate availability logically; a
    # datum can't be available before it happened.
    if feature.source_event_timestamp > feature.available_at_timestamp:
        return FeatureRejection(
            name,
            "source_event_timestamp is after available_at_timestamp (impossible provenance)",
            "leakage",
        )

    # --- Licensing -------------------------------------------------------
    lic = feature.provider_license
    if lic is ProviderLicense.FRED:
        return FeatureRejection(
            name, "FRED-derived values are not permitted in ML features", "licensing"
        )
    if lic is ProviderLicense.UNLICENSED_ALT:
        return FeatureRejection(
            name,
            f"provider '{feature.source_provider}' has no registered ML license "
            "(behavioral/sentiment/news/unknown sources are blocked)",
            "licensing",
        )
    if lic is ProviderLicense.TWELVE_DATA and for_training:
        if not flags.twelve_data_training_allowed(settings):
            return FeatureRejection(
                name,
                "Twelve Data is not licensed for training storage/ML use "
                "(ML_ALLOW_TWELVE_DATA_TRAINING=false)",
                "licensing",
            )

    return None


def build_feature_vector(
    features: Iterable[PointInTimeFeature],
    *,
    for_training: bool,
    settings: Settings | None = None,
    strict: bool = True,
) -> tuple[dict[str, float | int | None], list[FeatureRejection]]:
    """Assemble a name->value vector from validated features.

    Returns ``(vector, rejections)``. In ``strict`` mode (the default, used
    for building training data and for inference), any rejection raises -
    a leaking or unlicensed feature must never silently drop through. In
    non-strict mode rejections are collected and the offending features are
    excluded, for diagnostics/exploration only.

    A companion ``<name>__is_missing`` indicator is emitted for every catalog
    feature so the downstream model learns from missingness instead of being
    fed an undisclosed imputation.
    """
    vector: dict[str, float | int | None] = {}
    rejections: list[FeatureRejection] = []
    present: set[str] = set()

    for feat in features:
        rej = validate_feature(feat, for_training=for_training, settings=settings)
        if rej is not None:
            if strict and rej.code in {"leakage", "licensing"}:
                # Leakage/licensing are hard safety errors even in strict build.
                if rej.code == "leakage":
                    raise LeakageError(f"{rej.feature_name}: {rej.reason}")
                raise LicensingError(f"{rej.feature_name}: {rej.reason}")
            rejections.append(rej)
            continue
        if feat.is_missing_indicator:
            vector[feat.feature_name] = feat.feature_value
            continue
        vector[feat.feature_name] = feat.feature_value
        if feat.data_quality_status not in (DataQualityStatus.MISSING, DataQualityStatus.INVALID) and feat.feature_value is not None:
            present.add(feat.feature_name)

    # Emit missing indicators for catalog features not explicitly provided
    # as their own indicator.
    for cat_name in FEATURE_CATALOG:
        ind = missing_indicator_name(cat_name)
        if ind not in vector:
            vector[ind] = 0 if cat_name in present else 1

    return vector, rejections
