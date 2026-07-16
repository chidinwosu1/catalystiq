"""ORM models matching the build spec's schema sketch (§7).

Table shapes follow §7 directly; types/constraints are filled in since the
spec only sketches column names. `behavioral_events` and
`reinforcement_stats` back the FBA engine (§3); the rest back the core
analytical engine (§2).
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from catalystiq.db.base import Base


class Ticker(Base):
    __tablename__ = "tickers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(15), unique=True, index=True)
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(100), nullable=True)

    price_history: Mapped[list["PriceHistory"]] = relationship(
        back_populates="ticker", cascade="all, delete-orphan"
    )


class PriceHistory(Base):
    __tablename__ = "price_history"
    __table_args__ = (UniqueConstraint("ticker_id", "date", name="uq_price_history_ticker_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    date: Mapped[dt.date] = mapped_column(index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(Integer)

    ticker: Mapped["Ticker"] = relationship(back_populates="price_history")


class IndicatorSnapshot(Base):
    __tablename__ = "indicator_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "ticker_id", "date", "indicator_name", name="uq_indicator_snapshot"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    date: Mapped[dt.date] = mapped_column(index=True)
    indicator_name: Mapped[str] = mapped_column(String(100), index=True)
    value: Mapped[float] = mapped_column(Float)
    percentile_5y: Mapped[float | None] = mapped_column(Float, nullable=True)


class OptionsSnapshot(Base):
    __tablename__ = "options_snapshots"
    __table_args__ = (UniqueConstraint("ticker_id", "date", name="uq_options_snapshot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    date: Mapped[dt.date] = mapped_column(index=True)
    iv: Mapped[float | None] = mapped_column(Float, nullable=True)
    iv_rank: Mapped[float | None] = mapped_column(Float, nullable=True)
    put_call_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_pain: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_move: Mapped[float | None] = mapped_column(Float, nullable=True)


class NewsEvent(Base):
    __tablename__ = "news_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    date: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    headline: Mapped[str] = mapped_column(String(500))
    source_url: Mapped[str] = mapped_column(String(1000))
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)


class BehavioralEvent(Base):
    """One detected ABC (Antecedent -> Behavior -> Consequence) instance (§3.1)."""

    __tablename__ = "behavioral_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    date: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    antecedent_tags: Mapped[list[str]] = mapped_column(JSON)
    behavior_tag: Mapped[str] = mapped_column(String(100), index=True)
    consequence_tag: Mapped[str | None] = mapped_column(String(100), nullable=True)
    magnitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    detection_rule_id: Mapped[str] = mapped_column(String(100))


class ReinforcementStat(Base):
    """Empirical reinforcement schedule for an antecedent/behavior/consequence triple (§3.2.3).

    Scoped to either a ticker or a sector via `scope_type`/`scope_id`, since
    the spec allows lookups at the ticker or sector/peer-group level.
    """

    __tablename__ = "reinforcement_stats"
    __table_args__ = (
        UniqueConstraint(
            "scope_type",
            "scope_id",
            "antecedent_tag",
            "behavior_tag",
            "consequence_tag",
            name="uq_reinforcement_stat",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(10))  # "ticker" | "sector"
    scope_id: Mapped[int] = mapped_column(Integer, index=True)
    antecedent_tag: Mapped[str] = mapped_column(String(100), index=True)
    behavior_tag: Mapped[str] = mapped_column(String(100), index=True)
    consequence_tag: Mapped[str] = mapped_column(String(100))
    occurrence_count: Mapped[int] = mapped_column(Integer)
    follow_through_rate: Mapped[float] = mapped_column(Float)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    timeframe: Mapped[str] = mapped_column(String(50))
    generated_at: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    rating: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[float] = mapped_column(Float)
    bullish_pct: Mapped[float] = mapped_column(Float)
    neutral_pct: Mapped[float] = mapped_column(Float)
    bearish_pct: Mapped[float] = mapped_column(Float)
    report_json: Mapped[dict] = mapped_column(JSON)
