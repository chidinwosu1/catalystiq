"""Concrete point-in-time feature provider over validated Silver storage.

``SilverPitFeatureProvider`` implements the ML foundation's provider-neutral
:class:`~catalystiq.ml.features.provider.PointInTimeFeatureProvider` Protocol by
reading **availability-filtered** ``SilverPriceBar`` rows and computing the
price-derived feature groups the manifest marks as
``integration_exists_not_point_in_time``.

Point-in-time guarantees (why this is not a leaking read):

* ``get_features`` only ever sees bars whose ``source_available_at`` is at or
  before the requested ``prediction_timestamp``. A bar with an unknown
  availability is **excluded** (fail closed) - we cannot prove it was knowable.
* Every returned :class:`PointInTimeFeature` is stamped with real provenance
  drawn from the newest *visible* bar, so the ML schema's own leakage gate
  (``available_at <= prediction_timestamp``) passes as defense in depth.
* Inputs we do not have a wired point-in-time source for are returned as
  features with ``data_quality_status = MISSING`` and ``feature_value = None``
  - **recorded, never fabricated** - so the dataset builder logs them as
  requirement gaps instead of training on invented numbers.

``get_executable_entry`` / ``get_forward_path`` read *forward* bars and are used
ONLY for offline label generation, never at inference time; they are therefore
intentionally not availability-filtered against the prediction timestamp.

This module reads the database and the analysis engine but imports only the ML
*schema* (the contract), never ML *model* code, and does not alter the ML
contract or the manifest.
"""
from __future__ import annotations

import datetime as dt
import math

from sqlalchemy.orm import Session

from catalystiq.analysis.indicators import compute_technical_snapshot
from catalystiq.db import models
from catalystiq.ml.features.provider import PointInTimeFeatureProvider
from catalystiq.ml.features.schema import (
    FEATURE_CATALOG,
    DataQualityStatus,
    PointInTimeFeature,
)
from catalystiq.ml.labels.barriers import Bar
from catalystiq.schemas.market_data import OHLCVBar

_UTC = dt.timezone.utc

# Underlying licensed source of the price bars (canonical id; OPEN in the ML
# license map). Price-derived features are deterministic transforms of it.
_PRICE_PROVIDER = "yahoo"
# Provider id for values Catalyst IQ itself determines (freshness, and the
# "this input is missing" determination). OPEN in the ML license map.
_COMPUTED_PROVIDER = "computed"

# Catalog feature name -> the reading name emitted by compute_technical_snapshot.
_SNAPSHOT_READING = {
    "sma_20": "sma_20",
    "sma_50": "sma_50",
    "sma_200": "sma_200",
    "price_vs_sma_50": "price_vs_sma_50_pct",
    "sma_50_slope": "sma_50_slope_10d_pct",
    "rsi_14": "rsi_14",
    "macd": "macd_line",
    "macd_signal": "macd_signal",
    "macd_hist": "macd_histogram",
    "atr_14": "atr_14",
    "realized_vol_20d": "realized_volatility_20d_annualized_pct",
    "relative_volume_20d": "relative_volume_20d_pct",
}


def _to_aware_utc(ts: dt.datetime) -> dt.datetime:
    return ts.replace(tzinfo=_UTC) if ts.tzinfo is None else ts.astimezone(_UTC)


def _to_naive_utc(ts: dt.datetime) -> dt.datetime:
    aware = _to_aware_utc(ts)
    return aware.replace(tzinfo=None)


def _eod(day: dt.date) -> dt.datetime:
    """End-of-day availability floor for a daily bar (matches the pipeline)."""
    return dt.datetime.combine(day, dt.time(23, 59, 59), tzinfo=_UTC)


class _Anchor:
    """The point-in-time provenance stamp shared by every feature in one call:
    drawn from the newest bar visible at the prediction timestamp."""

    __slots__ = ("source_event", "available_at", "retrieved_at")

    def __init__(self, source_event, available_at, retrieved_at):
        self.source_event = source_event
        self.available_at = available_at
        self.retrieved_at = retrieved_at


class SilverPitFeatureProvider(PointInTimeFeatureProvider):
    """Read-through, look-ahead-free feature provider backed by Silver bars.

    Parameters
    ----------
    db:
        An open SQLAlchemy session scoped to the caller's request/job.
    benchmark_symbol:
        Symbol used for market/relative-strength/beta features. When its Silver
        history is not available point-in-time, those features are emitted as
        MISSING rather than skipped.
    """

    def __init__(self, db: Session, *, benchmark_symbol: str = "SPY") -> None:
        self.db = db
        self.benchmark_symbol = benchmark_symbol.upper()

    # --- Protocol: features --------------------------------------------------

    def get_features(
        self, symbol: str, prediction_timestamp: dt.datetime
    ) -> list[PointInTimeFeature]:
        symbol = symbol.upper()
        pred = _to_aware_utc(prediction_timestamp)
        rows = self._visible_rows(symbol, pred)

        if not rows:
            # No knowable history: every catalog feature is honestly MISSING,
            # stamped against the prediction instant (no bar to draw from).
            anchor = _Anchor(pred, pred, pred)
            return self._all_missing(symbol, pred, anchor)

        anchor = self._anchor_from(rows[-1])
        bars = [_ohlcv(r) for r in rows]
        closes = [b.close for b in bars]
        opens = [b.open for b in bars]
        volumes = [b.volume for b in bars]

        computed: dict[str, float | None] = {}

        # --- indicator-based features (reuse the analysis engine) ------------
        snapshot = compute_technical_snapshot(symbol, bars)
        readings = {r.name: r for r in snapshot.indicators}
        for cat_name, reading_name in _SNAPSHOT_READING.items():
            reading = readings.get(reading_name)
            if reading is not None and reading.status == "computed" and reading.value is not None:
                computed[cat_name] = float(reading.value)

        # --- price / return / momentum / gap / volume (direct) ---------------
        computed["adj_close"] = closes[-1]
        computed["adj_open"] = opens[-1]
        computed["adj_high"] = bars[-1].high
        computed["adj_low"] = bars[-1].low
        computed["log_return_1d"] = _log_return(closes, 1)
        computed["log_return_5d"] = _log_return(closes, 5)
        computed["log_return_20d"] = _log_return(closes, 20)
        computed["momentum_20d"] = _simple_return(closes, 20)
        computed["momentum_60d"] = _simple_return(closes, 60)
        computed["overnight_gap_pct"] = _overnight_gap_pct(opens, closes)
        dollar_vol = _avg_dollar_volume(closes, volumes, 20)
        computed["dollar_volume_20d"] = dollar_vol
        computed["adv_dollar_20d"] = dollar_vol

        # --- benchmark-relative features (cross-symbol, still PIT) ------------
        computed.update(self._benchmark_features(symbol, pred, closes))

        # --- data quality / freshness ----------------------------------------
        freshness_days = (pred.date() - rows[-1].date).days
        computed["feature_freshness_days"] = float(max(freshness_days, 0))

        features: list[PointInTimeFeature] = []
        present = 0
        for name in FEATURE_CATALOG:
            if name in ("feature_completeness",):
                continue  # computed last, needs the present-count
            value = computed.get(name)
            provider = _COMPUTED_PROVIDER if name.startswith("feature_") else _PRICE_PROVIDER
            if value is None or (isinstance(value, float) and math.isnan(value)):
                features.append(self._missing(symbol, pred, name, anchor))
            else:
                present += 1
                features.append(
                    self._ok(symbol, pred, name, float(value), anchor, provider=provider)
                )

        # completeness is the fraction of catalog features we actually produced.
        total = len(FEATURE_CATALOG) - 1  # excluding feature_completeness itself
        completeness = present / total if total else 0.0
        features.append(
            self._ok(
                symbol, pred, "feature_completeness", round(completeness, 4), anchor,
                provider=_COMPUTED_PROVIDER,
            )
        )
        return features

    # --- Protocol: labels (offline, forward-looking) -------------------------

    def get_executable_entry(
        self, symbol: str, prediction_timestamp: dt.datetime
    ) -> tuple[dt.datetime, float] | None:
        symbol = symbol.upper()
        pred_date = _to_aware_utc(prediction_timestamp).date()
        ticker = self._ticker(symbol)
        if ticker is None:
            return None
        nxt = (
            self.db.query(models.SilverPriceBar)
            .filter(models.SilverPriceBar.ticker_id == ticker.id)
            .filter(models.SilverPriceBar.date > pred_date)
            .order_by(models.SilverPriceBar.date)
            .first()
        )
        if nxt is None or nxt.open is None:
            return None
        entry_session = dt.datetime.combine(nxt.date, dt.time(0, 0), tzinfo=_UTC)
        return entry_session, float(nxt.open)

    def get_forward_path(
        self, symbol: str, entry_session: dt.datetime, horizon_days: int
    ) -> list[Bar]:
        symbol = symbol.upper()
        start = _to_aware_utc(entry_session).date()
        ticker = self._ticker(symbol)
        if ticker is None:
            return []
        rows = (
            self.db.query(models.SilverPriceBar)
            .filter(models.SilverPriceBar.ticker_id == ticker.id)
            .filter(models.SilverPriceBar.date >= start)
            .order_by(models.SilverPriceBar.date)
            .limit(max(horizon_days, 0))
            .all()
        )
        # `session` is an opaque, orderable key used only for provenance/ordering
        # on the holding path; the bar's own date serves that role.
        return [
            Bar(open=r.open, high=r.high, low=r.low, close=r.close, session=r.date)
            for r in rows
        ]

    # --- internals -----------------------------------------------------------

    def _ticker(self, symbol: str):
        return self.db.query(models.Ticker).filter_by(symbol=symbol).one_or_none()

    def _visible_rows(self, symbol: str, pred: dt.datetime) -> list[models.SilverPriceBar]:
        """Bars for `symbol` whose availability is at/before `pred`. Bars with an
        unknown availability are excluded (fail closed)."""
        ticker = self._ticker(symbol)
        if ticker is None:
            return []
        cutoff = _to_naive_utc(pred)
        return (
            self.db.query(models.SilverPriceBar)
            .filter(models.SilverPriceBar.ticker_id == ticker.id)
            .filter(models.SilverPriceBar.source_available_at.isnot(None))
            .filter(models.SilverPriceBar.source_available_at <= cutoff)
            .order_by(models.SilverPriceBar.date)
            .all()
        )

    def _anchor_from(self, row: models.SilverPriceBar) -> _Anchor:
        available = (
            _to_aware_utc(row.source_available_at)
            if row.source_available_at is not None
            else _eod(row.date)
        )
        retrieved = _to_aware_utc(row.updated_at) if row.updated_at is not None else available
        if retrieved < available:
            retrieved = available
        return _Anchor(source_event=_eod(row.date), available_at=available, retrieved_at=retrieved)

    def _benchmark_features(
        self, symbol: str, pred: dt.datetime, closes: list[float]
    ) -> dict[str, float | None]:
        """market_return_20d, relative_strength_60d, beta_60d from the benchmark's
        point-in-time closes. Returns Nones (-> MISSING) when unavailable."""
        out: dict[str, float | None] = {
            "market_return_20d": None,
            "relative_strength_60d": None,
            "beta_60d": None,
        }
        if symbol == self.benchmark_symbol:
            return out
        bench_rows = self._visible_rows(self.benchmark_symbol, pred)
        bench_closes = [r.close for r in bench_rows]
        out["market_return_20d"] = _simple_return(bench_closes, 20)
        sym_60 = _simple_return(closes, 60)
        bench_60 = _simple_return(bench_closes, 60)
        if sym_60 is not None and bench_60 is not None:
            out["relative_strength_60d"] = sym_60 - bench_60
        out["beta_60d"] = _beta(closes, bench_closes, 60)
        return out

    def _all_missing(
        self, symbol: str, pred: dt.datetime, anchor: _Anchor
    ) -> list[PointInTimeFeature]:
        feats = [self._missing(symbol, pred, name, anchor) for name in FEATURE_CATALOG if name != "feature_completeness"]
        feats.append(self._ok(symbol, pred, "feature_completeness", 0.0, anchor, provider=_COMPUTED_PROVIDER))
        return feats

    def _ok(
        self, symbol, pred, name, value, anchor: _Anchor, *, provider: str
    ) -> PointInTimeFeature:
        return PointInTimeFeature(
            symbol=symbol,
            prediction_timestamp=pred,
            feature_name=name,
            feature_value=value,
            source_provider=provider,
            source_event_timestamp=anchor.source_event,
            available_at_timestamp=anchor.available_at,
            retrieved_at_timestamp=anchor.retrieved_at,
            data_quality_status=DataQualityStatus.OK,
        )

    def _missing(self, symbol, pred, name, anchor: _Anchor) -> PointInTimeFeature:
        return PointInTimeFeature(
            symbol=symbol,
            prediction_timestamp=pred,
            feature_name=name,
            feature_value=None,
            source_provider=_COMPUTED_PROVIDER,
            source_event_timestamp=anchor.source_event,
            available_at_timestamp=anchor.available_at,
            retrieved_at_timestamp=anchor.retrieved_at,
            data_quality_status=DataQualityStatus.MISSING,
        )


# --- pure numeric helpers ----------------------------------------------------


def _ohlcv(row: models.SilverPriceBar) -> OHLCVBar:
    return OHLCVBar(
        date=row.date, open=row.open, high=row.high, low=row.low, close=row.close, volume=row.volume
    )


def _log_return(closes: list[float], lag: int) -> float | None:
    if len(closes) <= lag or closes[-1 - lag] <= 0 or closes[-1] <= 0:
        return None
    return math.log(closes[-1] / closes[-1 - lag])


def _simple_return(closes: list[float], lag: int) -> float | None:
    if len(closes) <= lag or closes[-1 - lag] <= 0:
        return None
    return closes[-1] / closes[-1 - lag] - 1.0


def _overnight_gap_pct(opens: list[float], closes: list[float]) -> float | None:
    if len(closes) < 2 or closes[-2] <= 0:
        return None
    return (opens[-1] / closes[-2] - 1.0) * 100.0


def _avg_dollar_volume(closes: list[float], volumes: list[int], window: int) -> float | None:
    if len(closes) < window:
        return None
    recent = list(zip(closes[-window:], volumes[-window:]))
    return sum(c * v for c, v in recent) / window


def _beta(sym_closes: list[float], bench_closes: list[float], window: int) -> float | None:
    """OLS beta of the symbol's daily returns vs the benchmark's over `window`
    overlapping sessions. Returns None if either series is too short."""
    n = window + 1
    if len(sym_closes) < n or len(bench_closes) < n:
        return None
    sym = _daily_returns(sym_closes[-n:])
    bench = _daily_returns(bench_closes[-n:])
    if len(sym) != len(bench) or len(bench) < 2:
        return None
    mean_b = sum(bench) / len(bench)
    mean_s = sum(sym) / len(sym)
    cov = sum((b - mean_b) * (s - mean_s) for b, s in zip(bench, sym))
    var = sum((b - mean_b) ** 2 for b in bench)
    if var == 0:
        return None
    return cov / var


def _daily_returns(closes: list[float]) -> list[float]:
    out = []
    for prev, cur in zip(closes[:-1], closes[1:]):
        if prev and prev > 0:
            out.append(cur / prev - 1.0)
    return out
