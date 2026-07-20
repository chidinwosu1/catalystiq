"""Concrete point-in-time feature provider over validated Silver data.

This is the first real :class:`~catalystiq.ml.features.provider.PointInTimeFeatureProvider`
implementation. It is deliberately built on the app's OWN persisted, validated
data and computation layer - it does NOT call Yahoo/Twelve Data/SEC/etc.
directly and does not modify those integrations:

  * price/technical/volatility/volume/liquidity/market-context features come
    from the existing analysis snapshots computed on validated Silver bars;
  * the rule-based Opportunity Score (and its factor sub-scores) come from the
    published ``build_opportunity_score`` contract;
  * everything is computed from bars truncated to the last CLOSED session at or
    before ``prediction_timestamp`` - so the same feature vector is produced
    regardless of whether future bars happen to exist in the database. That
    look-ahead invariance is the whole point, and it is asserted in tests.

Features with no validated point-in-time source (market regime, earnings
proximity, point-in-time fundamentals, macro vintages, support/resistance
distances) are emitted with ``data_quality_status = MISSING`` - RECORDED as a
gap, never fabricated. The dataset builder surfaces those as requirement gaps
and the feature manifest tracks which groups are wired.

Training/inference remain gated by the ML flags; this module is offline
infrastructure and does not itself enable anything.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Callable

from catalystiq.analysis.opportunity_score import build_opportunity_score
from catalystiq.pipelines.freshness import FreshnessPolicy
from catalystiq.schemas.market_data import OHLCVBar
from catalystiq.ml.features.schema import DataQualityStatus, PointInTimeFeature
from catalystiq.ml.features.fundamentals_pit import pit_fundamental_features
from catalystiq.ml.features.macro_pit import pit_macro_features
from catalystiq.ml.labels.barriers import Bar

# Provider label recorded on every computed feature. "computed" is an OPEN
# licensing class in the feature schema (our own derived values), so these
# features pass the licensing gate. Underlying inputs are validated Silver.
_PROVIDER = "computed"


def _reading(readings, name):
    for r in readings:
        if getattr(r, "name", None) == name:
            return r
    return None


def _val(readings, name, *, scale: float = 1.0):
    r = _reading(readings, name)
    if r is None:
        return None
    if getattr(r, "value", None) is None:
        return None
    if getattr(r, "status", None) not in (None, "available", "computed"):
        return None
    try:
        return float(r.value) * scale
    except (TypeError, ValueError):
        return None


def _log_return(bars: list[OHLCVBar], lookback: int) -> float | None:
    if len(bars) <= lookback:
        return None
    prev = bars[-1 - lookback].close
    last = bars[-1].close
    if prev is None or last is None or prev <= 0 or last <= 0:
        return None
    return math.log(last / prev)


def _overnight_gap_pct(bars: list[OHLCVBar]) -> float | None:
    if len(bars) < 2:
        return None
    prev_close = bars[-2].close
    today_open = bars[-1].open
    if not prev_close or prev_close <= 0 or today_open is None:
        return None
    return (today_open - prev_close) / prev_close * 100.0


def _window_return(bars: list[OHLCVBar], lookback: int) -> float | None:
    if len(bars) <= lookback:
        return None
    prev = bars[-1 - lookback].close
    last = bars[-1].close
    if not prev or prev <= 0:
        return None
    return (last - prev) / prev


class SilverPointInTimeProvider:
    """Point-in-time features from validated Silver bars + analysis snapshots.

    Parameters
    ----------
    db:
        A SQLAlchemy session used only to READ Silver (never to fetch/ingest).
    retrieved_at:
        The wall-clock stamp recorded as ``retrieved_at_timestamp`` on every
        feature (injected for reproducibility; defaults to the prediction time).
    benchmark_symbol / sector_resolver:
        Optional market/sector context for relative-strength & beta features.
        When their Silver bars are absent, those features are emitted MISSING.
    """

    def __init__(
        self,
        db,
        *,
        benchmark_symbol: str = "SPY",
        sector_resolver: Callable[[str], str | None] | None = None,
        freshness_policy: FreshnessPolicy | None = None,
        retrieved_at: dt.datetime | None = None,
        bars_loader: Callable[[str, object], list[OHLCVBar]] | None = None,
    ) -> None:
        self.db = db
        self.benchmark_symbol = benchmark_symbol
        self.sector_resolver = sector_resolver or _default_sector_resolver
        self.freshness_policy = freshness_policy or FreshnessPolicy()
        self.retrieved_at = retrieved_at
        # Injectable for tests; defaults to the real Silver read path.
        self._bars_loader = bars_loader or _default_bars_loader

    # -- bar access -------------------------------------------------------
    def _all_bars(self, symbol: str) -> list[OHLCVBar]:
        return self._bars_loader(symbol, self.db)

    def _bars_asof(self, symbol: str, prediction_timestamp: dt.datetime) -> list[OHLCVBar]:
        """Bars up to and including the last CLOSED session at/before the
        prediction timestamp. Excludes any unclosed/future candle."""
        last_closed = self.freshness_policy.latest_expected_session(prediction_timestamp)
        bars = self._all_bars(symbol)
        if last_closed is None:
            return bars
        return [b for b in bars if b.date <= last_closed]

    # -- provider protocol ------------------------------------------------
    def get_features(self, symbol: str, prediction_timestamp: dt.datetime) -> list[PointInTimeFeature]:
        bars = self._bars_asof(symbol, prediction_timestamp)
        retrieved = self.retrieved_at or prediction_timestamp

        # Provenance timestamps shared by every feature in this vector.
        if bars:
            event_ts = dt.datetime.combine(bars[-1].date, dt.time(), tzinfo=prediction_timestamp.tzinfo)
        else:
            event_ts = prediction_timestamp

        def mk(name: str, value, status: DataQualityStatus = DataQualityStatus.OK) -> PointInTimeFeature:
            if value is None and status is DataQualityStatus.OK:
                status = DataQualityStatus.MISSING
            return PointInTimeFeature(
                symbol=symbol.upper(),
                prediction_timestamp=prediction_timestamp,
                feature_name=name,
                feature_value=value,
                source_provider=_PROVIDER,
                source_event_timestamp=event_ts,
                available_at_timestamp=prediction_timestamp,
                retrieved_at_timestamp=retrieved,
                data_quality_status=status,
            )

        features: list[PointInTimeFeature] = []

        if not bars:
            # No closed history: everything missing, honestly recorded.
            for name in _ALL_CATALOG_NAMES:
                features.append(mk(name, None, DataQualityStatus.MISSING))
            return features

        # Compute the analysis snapshots + rule-based score ONCE on the
        # truncated (point-in-time) bars.
        snaps = _compute_snapshots(symbol, bars, prediction_timestamp,
                                   self.benchmark_symbol, self.sector_resolver,
                                   self._bars_asof)
        tech, risk, vol, ctx = snaps.tech, snaps.risk, snaps.vol, snaps.ctx

        last = bars[-1]

        # --- price / OHLCV -----------------------------------------------
        features += [
            mk("adj_close", last.close),
            mk("adj_open", last.open),
            mk("adj_high", last.high),
            mk("adj_low", last.low),
            mk("log_return_1d", _log_return(bars, 1)),
            mk("log_return_5d", _log_return(bars, 5)),
            mk("log_return_20d", _log_return(bars, 20)),
        ]
        # --- trend --------------------------------------------------------
        features += [
            mk("sma_20", _val(tech.indicators, "sma_20")),
            mk("sma_50", _val(tech.indicators, "sma_50")),
            mk("sma_200", _val(tech.indicators, "sma_200")),
            mk("price_vs_sma_50", _val(tech.indicators, "price_vs_sma_50_pct", scale=0.01)),
            mk("sma_50_slope", _val(tech.indicators, "sma_50_slope_10d_pct")),
        ]
        # --- momentum -----------------------------------------------------
        features += [
            mk("momentum_20d", _window_return(bars, 20)),
            mk("momentum_60d", _window_return(bars, 60)),
        ]
        # --- oscillators --------------------------------------------------
        features += [
            mk("rsi_14", _val(tech.indicators, "rsi_14")),
            mk("macd", _val(tech.indicators, "macd_line")),
            mk("macd_signal", _val(tech.indicators, "macd_signal")),
            mk("macd_hist", _val(tech.indicators, "macd_histogram")),
        ]
        # --- volatility ---------------------------------------------------
        features += [
            mk("atr_14", _val(tech.indicators, "atr_14")),
            mk("realized_vol_20d", _val(tech.indicators, "realized_volatility_20d_annualized_pct")),
        ]
        # --- volume / liquidity ------------------------------------------
        rel_vol = _val(tech.indicators, "relative_volume_20d_pct", scale=0.01)
        adv_dollar = _val(vol.metrics, "rolling_median_dollar_volume") if vol else None
        spread_bps = _val(vol.metrics, "spread_pct_of_mid", scale=100.0) if vol else None
        features += [
            mk("relative_volume_20d", rel_vol),
            mk("dollar_volume_20d", adv_dollar),
            mk("estimated_spread_bps", spread_bps),
            mk("adv_dollar_20d", adv_dollar),
        ]
        # --- gaps ---------------------------------------------------------
        features += [mk("overnight_gap_pct", _overnight_gap_pct(bars))]
        # --- support / resistance (no PIT mapping yet -> recorded missing)-
        features += [
            mk("dist_to_support_pct", None, DataQualityStatus.MISSING),
            mk("dist_to_resistance_pct", None, DataQualityStatus.MISSING),
        ]
        # --- market / sector / relative strength / beta ------------------
        features += [
            mk("market_return_20d", snaps.market_return_20d),
            mk("sector_return_20d", snaps.sector_return_20d),
            mk("relative_strength_60d", _val(ctx.metrics, "relative_return_60d_vs_market") if ctx else None),
            mk("beta_60d", _val(risk.metrics, "beta_vs_benchmark") if risk else None),
        ]
        # --- market regime (point-in-time, from validated benchmark bars) -
        regime_code = snaps.regime.code if snaps.regime and snaps.regime.available else None
        features.append(mk("market_regime", float(regime_code) if regime_code is not None else None))

        # --- SEC fundamentals (vintage- and amendment-correct, point-in-time)
        last_closed = self.freshness_policy.latest_expected_session(prediction_timestamp)
        as_of_date = last_closed or bars[-1].date
        features += pit_fundamental_features(
            self.db, symbol, prediction_timestamp, as_of=as_of_date, retrieved_at=retrieved
        )
        # --- BLS / BEA macro (strict vintage read; fails closed) ----------
        features += pit_macro_features(
            self.db, symbol, prediction_timestamp, as_of=as_of_date, retrieved_at=retrieved
        )
        # --- earnings proximity: no legitimate timestamped source yet -----
        features.append(mk("trading_days_to_earnings", None, DataQualityStatus.MISSING))

        # --- rule-based opportunity score + factors ----------------------
        features += _rule_based_features(mk, snaps.opportunity)

        # --- data quality / freshness ------------------------------------
        last_closed = self.freshness_policy.latest_expected_session(prediction_timestamp)
        freshness_days = (last_closed - last.date).days if last_closed else None
        present = sum(1 for f in features if f.feature_value is not None
                      and f.data_quality_status is DataQualityStatus.OK)
        completeness = present / max(1, len(features))
        features += [
            mk("feature_freshness_days", float(freshness_days) if freshness_days is not None else None),
            mk("feature_completeness", round(completeness, 4)),
        ]
        return features

    def get_executable_entry(self, symbol: str, prediction_timestamp: dt.datetime):
        """Next session's executable open AFTER the prediction timestamp.

        Offline (historical) this reads the next stored Silver bar; at true
        live inference that bar does not exist yet, so this returns None -
        entry is never assumed at a price already known at prediction time.
        """
        last_closed = self.freshness_policy.latest_expected_session(prediction_timestamp)
        for b in self._all_bars(symbol):
            if last_closed is None or b.date > last_closed:
                if b.open is None:
                    return None
                entry_session = dt.datetime.combine(b.date, dt.time(), tzinfo=prediction_timestamp.tzinfo)
                return (entry_session, float(b.open))
        return None

    def get_forward_path(self, symbol: str, entry_session: dt.datetime, horizon_days: int) -> list[Bar]:
        """OHLC bars from the entry session forward across ``horizon_days``
        sessions (offline label generation only)."""
        entry_date = entry_session.date() if isinstance(entry_session, dt.datetime) else entry_session
        forward = [b for b in self._all_bars(symbol) if b.date >= entry_date]
        forward = forward[: max(0, horizon_days)]
        return [Bar(open=b.open, high=b.high, low=b.low, close=b.close, session=b.date) for b in forward]


# --- snapshot computation ---------------------------------------------------
class _Snapshots:
    __slots__ = ("tech", "risk", "vol", "ctx", "opportunity", "regime",
                 "market_return_20d", "sector_return_20d")


def _compute_snapshots(symbol, bars, prediction_timestamp, benchmark_symbol, sector_resolver, bars_asof):
    from catalystiq.analysis.indicators import compute_technical_snapshot
    from catalystiq.analysis.market_context import compute_market_context_snapshot
    from catalystiq.analysis.risk import compute_risk_snapshot
    from catalystiq.analysis.volume_liquidity import compute_volume_liquidity_snapshot

    s = _Snapshots()
    s.tech = compute_technical_snapshot(symbol, bars)
    s.risk = compute_risk_snapshot(symbol, bars)
    s.vol = compute_volume_liquidity_snapshot(symbol, bars)

    market_bars = bars_asof(benchmark_symbol, prediction_timestamp) if benchmark_symbol else []
    sector_symbol = sector_resolver(symbol) if sector_resolver else None
    sector_bars = bars_asof(sector_symbol, prediction_timestamp) if sector_symbol else []

    s.ctx = compute_market_context_snapshot(
        symbol, bars, market_bars=market_bars or None, market_symbol=benchmark_symbol,
        sector_bars=sector_bars or None, sector_symbol=sector_symbol,
    )
    s.market_return_20d = _window_return(market_bars, 20) if market_bars else None
    s.sector_return_20d = _window_return(sector_bars, 20) if sector_bars else None

    from catalystiq.ml.features.regime import classify_market_regime

    s.regime = classify_market_regime(market_bars, symbol=benchmark_symbol) if market_bars else None

    s.opportunity = build_opportunity_score(
        symbol, bars, now=prediction_timestamp,
        market_bars=market_bars or None, market_symbol=benchmark_symbol,
        sector_bars=sector_bars or None, sector_symbol=sector_symbol,
    )
    return s


def _rule_based_features(mk, opportunity):
    """Map the OpportunityScore contract to the rule-based catalog features.

    Only an ``available`` score contributes a value; ``insufficient_data`` is
    recorded MISSING (never a zero-filled bearish default)."""
    from catalystiq.ml.features.schema import DataQualityStatus

    setup = opportunity.score if opportunity and opportunity.status == "available" else None
    factors = {f.name: f.score for f in (opportunity.factors if opportunity else [])
               if f.status == "available"}
    return [
        mk("rule_based_setup_strength", float(setup) if setup is not None else None),
        mk("rule_based_trend_factor", _f(factors, "trend")),
        mk("rule_based_momentum_factor", _f(factors, "momentum")),
        mk("rule_based_volume_factor", _f(factors, "volume_liquidity")),
    ]


def _f(factors: dict, name: str):
    v = factors.get(name)
    return float(v) if v is not None else None


def _default_bars_loader(symbol: str, db) -> list[OHLCVBar]:
    from catalystiq.pipelines.market_price_pipeline import get_silver_bars

    return get_silver_bars(symbol, db)


def _default_sector_resolver(symbol: str) -> str | None:
    from catalystiq.analysis.sectors import governed_sector_etf

    return governed_sector_etf(symbol)


# Full catalog name list used when there are no bars at all.
from catalystiq.ml.features.schema import FEATURE_CATALOG as _CATALOG  # noqa: E402

_ALL_CATALOG_NAMES = tuple(_CATALOG.keys())
