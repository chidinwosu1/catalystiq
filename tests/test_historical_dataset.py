"""Survivorship-bias-free historical dataset assembly.

Proves the assembler builds a point-in-time universe that INCLUDES symbols later
delisted, produces a real (non-synthetic) labeled dataset via the existing
builder, and never lets a delisted name leak into a universe dated after it
stopped trading.
"""
from __future__ import annotations

import datetime as dt

from catalystiq.db import models
from catalystiq.features import HistoricalDatasetAssembler, trading_timestamps
from catalystiq.ml.dataset.universe import UniverseConfig

_UTC = dt.timezone.utc
# Loose thresholds so a small seeded fixture is eligible.
_CFG = UniverseConfig(min_history_bars=25, min_avg_daily_dollar_volume=1_000.0, min_price=5.0)


def _eod(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(23, 59, 59), tzinfo=_UTC)


def _seed(session, symbol, start, n, *, active=True, base=100.0):
    ticker = models.Ticker(symbol=symbol)
    session.add(ticker)
    session.flush()
    day, added, dates = start, 0, []
    while added < n:
        if day.weekday() < 5:
            price = base + added * 0.3
            session.add(
                models.SilverPriceBar(
                    ticker_id=ticker.id, date=day,
                    open=price - 0.1, high=price + 1, low=price - 1, close=price,
                    volume=1_000_000, source_available_at=_eod(day).replace(tzinfo=None),
                    data_quality_status="ok",
                    created_at=dt.datetime(2020, 1, 1), updated_at=_eod(day).replace(tzinfo=None),
                )
            )
            dates.append(day)
            added += 1
        day += dt.timedelta(days=1)
    session.add(
        models.SilverSecurityMaster(
            stable_identifier=symbol, provider="nasdaq_trader",
            source_record_id=symbol, effective_at=None,
            retrieved_at=dt.datetime(2020, 1, 1), validation_status="clean",
            normalization_version="1.0.0", created_at=dt.datetime(2020, 1, 1),
            internal_security_id=symbol, symbol=symbol, name=symbol,
            exchange="NASDAQ", etf=False, is_active=active,
        )
    )
    session.commit()
    return dates


def test_delisted_symbol_is_included_while_it_traded(test_db_session):
    # SURV keeps trading; DEAD delists after ~40 sessions.
    surv_dates = _seed(test_db_session, "SURV", dt.date(2024, 1, 1), 80)
    dead_dates = _seed(test_db_session, "DEAD", dt.date(2024, 1, 1), 45, active=False)

    assembler = HistoricalDatasetAssembler(test_db_session, config=_CFG, horizon_days=5)
    # Rank on a date well inside DEAD's trading window.
    ts = _eod(dead_dates[34])
    symbols, decisions = assembler.universe_at(ts)
    assert "DEAD" in symbols  # not survivorship-filtered
    assert "SURV" in symbols


def test_delisted_symbol_excluded_after_it_stops_trading(test_db_session):
    _seed(test_db_session, "SURV", dt.date(2024, 1, 1), 80)
    dead_dates = _seed(test_db_session, "DEAD", dt.date(2024, 1, 1), 45, active=False)

    assembler = HistoricalDatasetAssembler(test_db_session, config=_CFG, horizon_days=5)
    # A ranking date well after DEAD's last bar: it is no longer tradable then.
    later = _eod(dead_dates[-1] + dt.timedelta(days=30))
    symbols, decisions = assembler.universe_at(later)
    assert "DEAD" not in symbols
    dead_decision = next(d for d in decisions if d.symbol == "DEAD")
    assert not dead_decision.eligible
    assert any("tradable" in r or "listed" in r for r in dead_decision.reasons)


def test_build_produces_real_labeled_dataset_including_delisted(test_db_session):
    _seed(test_db_session, "SURV", dt.date(2024, 1, 1), 80)
    dead_dates = _seed(test_db_session, "DEAD", dt.date(2024, 1, 1), 45, active=False)

    assembler = HistoricalDatasetAssembler(test_db_session, config=_CFG, horizon_days=5)
    # Prediction dates inside DEAD's window with room for a 5-session forward path.
    preds = [_eod(dead_dates[i]) for i in (30, 33, 36)]
    result = assembler.build(preds)

    assert result.size > 0
    assert result.dataset.is_synthetic is False  # real data
    assert "DEAD" in {e.symbol for e in result.dataset.examples}
    assert "DEAD" in result.delisted_included
    # Every example carries labels and a point-in-time feature vector.
    for ex in result.dataset.examples:
        assert ex.labels is not None
        assert ex.features.get("adj_close") is not None
        assert ex.entry_session.date() > ex.prediction_timestamp.date()  # forward entry


def test_trading_timestamps_uses_real_sessions(test_db_session):
    dates = _seed(test_db_session, "SURV", dt.date(2024, 1, 1), 30)
    ts = trading_timestamps(test_db_session, "SURV", dates[0], dates[-1], step=5)
    assert ts and all(t.tzinfo is not None for t in ts)
    assert [t.date() for t in ts] == dates[::5]
