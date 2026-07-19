"""Survivorship-bias-free historical dataset assembly.

Turns the validated Silver store into a real (non-synthetic), look-ahead-free
:class:`~catalystiq.ml.dataset.builder.TrainingDataset` spanning a date range.
Two responsibilities:

1. **Point-in-time universe.** For a ranking timestamp T it builds a
   :class:`~catalystiq.ml.dataset.universe.CandidateSnapshot` for every symbol in
   the Silver security master - crucially **including symbols later delisted** -
   using only data knowable at T (drawn through
   :class:`~catalystiq.features.pit_provider.SilverPitFeatureProvider`). A
   symbol's point-in-time *listed/tradable* status is inferred from whether it
   had a Silver bar available at/around T, so a name that delisted in 2023 still
   appears in 2022 universes. That is what keeps the dataset free of
   survivorship bias.

2. **Labeled examples.** It samples prediction timestamps across the range,
   forms one :class:`~catalystiq.ml.dataset.builder.ExampleRequest` per eligible
   member per date, and drives the existing ``TrainingExampleBuilder`` (which
   validates features through the ML schema and generates outcome labels from
   the executable next-session entry + forward path).

This module reads the database and the analysis engine but imports only the ML
*schema/builder* (the contract), never ML model code, and builds **no** model
and trains nothing. The resulting dataset is stamped ``is_synthetic=False``.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from catalystiq.db import models
from catalystiq.features.pit_provider import SilverPitFeatureProvider
from catalystiq.ml.dataset.builder import (
    ExampleRequest,
    TrainingDataset,
    TrainingExampleBuilder,
)
from catalystiq.ml.dataset.universe import (
    AssetType,
    CandidateSnapshot,
    EligibilityDecision,
    UniverseConfig,
    build_eligible_universe,
)

_UTC = dt.timezone.utc
# A symbol counts as point-in-time tradable at T only if its newest bar known at
# T is no older than this (a delisted name has no recent bar -> not tradable then).
_LISTED_RECENCY_DAYS = 7


def _eod(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(23, 59, 59), tzinfo=_UTC)


def _asset_type(sec: models.SilverSecurityMaster) -> AssetType:
    if sec.etf:
        return AssetType.ETF
    return AssetType.COMMON_STOCK


@dataclass
class HistoricalDatasetResult:
    dataset: TrainingDataset
    universe_decisions: dict[str, list[EligibilityDecision]] = field(default_factory=dict)
    delisted_included: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return self.dataset.size


class HistoricalDatasetAssembler:
    """Assembles a point-in-time, survivorship-bias-free training dataset from
    Silver storage."""

    def __init__(
        self,
        db: Session,
        *,
        config: UniverseConfig | None = None,
        benchmark_symbol: str = "SPY",
        horizon_days: int = 5,
    ) -> None:
        self.db = db
        self.config = config or UniverseConfig()
        self.provider = SilverPitFeatureProvider(db, benchmark_symbol=benchmark_symbol)
        self.horizon_days = horizon_days

    # --- point-in-time universe ---------------------------------------------

    def candidate_at(
        self, sec: models.SilverSecurityMaster, ranking_timestamp: dt.datetime
    ) -> CandidateSnapshot:
        """Build a point-in-time candidate snapshot for one security, using only
        data knowable at ``ranking_timestamp``."""
        symbol = sec.symbol.upper()
        rows = self.provider._visible_rows(symbol, ranking_timestamp)  # PIT bars
        history_bars = len(rows)

        price = adv = spread = None
        staleness: float | None = None
        listed = False
        tradable = False
        if rows:
            newest = rows[-1]
            price = float(newest.close)
            closes = [r.close for r in rows]
            vols = [r.volume for r in rows]
            window = min(20, len(rows))
            adv = sum(c * v for c, v in zip(closes[-window:], vols[-window:])) / window
            staleness = float((ranking_timestamp.date() - newest.date).days)
            # Point-in-time listed/tradable: had a *recent* bar at T. A delisted
            # name evaluated at a later T has no recent bar -> not tradable then,
            # but at an earlier T (when it still traded) it is included.
            listed = staleness <= _LISTED_RECENCY_DAYS
            tradable = listed

        return CandidateSnapshot(
            symbol=symbol,
            asset_type=_asset_type(sec),
            price=price,
            avg_daily_dollar_volume=adv,
            # We do not have a validated point-in-time quoted spread; leave it
            # None so the eligibility spread gate is not silently satisfied.
            estimated_spread_bps=spread,
            history_bars=history_bars,
            is_tradable=tradable,
            listed=listed,
            sector=None,
            feature_staleness_days=staleness,
        )

    def universe_at(
        self, ranking_timestamp: dt.datetime, *, require_spread: bool = False
    ) -> tuple[list[str], list[EligibilityDecision]]:
        """Return ``(eligible_symbols, decisions)`` as the universe existed at
        ``ranking_timestamp`` - including symbols later delisted."""
        secs = self.db.query(models.SilverSecurityMaster).all()
        snaps = [self.candidate_at(s, ranking_timestamp) for s in secs]
        if not require_spread:
            # Neutralize the quoted-spread gate we cannot source point-in-time,
            # so eligibility rests on price/liquidity/history we actually have.
            snaps = [
                CandidateSnapshot(**{**s.__dict__, "estimated_spread_bps": 0.0})
                if s.estimated_spread_bps is None
                else s
                for s in snaps
            ]
        members, decisions = build_eligible_universe(
            snaps, self.config, ranking_timestamp=ranking_timestamp
        )
        return [m.symbol for m in members], decisions

    # --- labeled dataset -----------------------------------------------------

    def build(
        self,
        prediction_timestamps: list[dt.datetime],
        *,
        direction: str = "long",
        require_spread: bool = False,
    ) -> HistoricalDatasetResult:
        """Assemble labeled examples across the given prediction timestamps.

        For each timestamp the point-in-time eligible universe is constructed
        (delisted names included) and one example per eligible symbol is built
        and labeled. Returns the real (non-synthetic) dataset plus the per-date
        eligibility decisions and the list of delisted names that were included.
        """
        builder = TrainingExampleBuilder(
            self.provider,
            for_training=True,
            is_synthetic=False,
            source_providers=["yahoo"],
        )
        requests: list[ExampleRequest] = []
        decisions_by_date: dict[str, list[EligibilityDecision]] = {}
        for ts in prediction_timestamps:
            ts = ts if ts.tzinfo else ts.replace(tzinfo=_UTC)
            symbols, decisions = self.universe_at(ts, require_spread=require_spread)
            decisions_by_date[ts.isoformat()] = decisions
            for sym in symbols:
                requests.append(
                    ExampleRequest(
                        symbol=sym,
                        prediction_timestamp=ts,
                        direction=direction,
                        horizon_days=self.horizon_days,
                    )
                )

        dataset = builder.build(requests)

        delisted = self._delisted_symbols_included(dataset)
        return HistoricalDatasetResult(
            dataset=dataset,
            universe_decisions=decisions_by_date,
            delisted_included=delisted,
        )

    def _delisted_symbols_included(self, dataset: TrainingDataset) -> list[str]:
        used = {e.symbol for e in dataset.examples}
        if not used:
            return []
        inactive = {
            s.symbol.upper()
            for s in self.db.query(models.SilverSecurityMaster)
            .filter(models.SilverSecurityMaster.is_active.is_(False))
            .all()
        }
        return sorted(used & inactive)


def trading_timestamps(
    db: Session,
    symbol: str,
    start: dt.date,
    end: dt.date,
    *,
    step: int = 1,
) -> list[dt.datetime]:
    """Convenience: the end-of-day timestamps of a reference symbol's Silver bars
    in ``[start, end]`` (every ``step``-th bar), to use as prediction timestamps.
    Uses real session dates, so non-trading days are skipped automatically."""
    ticker = db.query(models.Ticker).filter_by(symbol=symbol.upper()).one_or_none()
    if ticker is None:
        return []
    rows = (
        db.query(models.SilverPriceBar)
        .filter(models.SilverPriceBar.ticker_id == ticker.id)
        .filter(models.SilverPriceBar.date >= start)
        .filter(models.SilverPriceBar.date <= end)
        .order_by(models.SilverPriceBar.date)
        .all()
    )
    return [_eod(r.date) for r in rows[::step]]
