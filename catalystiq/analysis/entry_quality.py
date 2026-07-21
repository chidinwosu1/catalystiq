"""Dynamic Entry Quality Score - a real-time, intraday 0-100 read of whether
the *current moment* is an attractive entry, independent of the daily Setup
Strength (Rule-Based Opportunity Score).

Setup Strength answers *"Is this a high-quality STOCK to trade?"* over multiple
days. Entry Quality answers *"Is this a high-quality MOMENT to enter?"* right
now. A name can hold a 90/100 Setup Strength while having a poor Entry Quality
if it is extended after a large morning rally - so the two are scored, stored
and displayed separately.

Design rules (mirroring the Setup Strength engine's discipline):
  - 100-point weighting across seven intraday components:
      VWAP distance 20, 9-EMA distance 15, intraday RSI 15,
      time-since-pullback 10, relative volume 15, morning-range extension 15,
      risk/reward 10.
  - Built ONLY from real intraday OHLCV bars. No fabricated numbers.
  - A missing / insufficient intraday input NEVER counts as a bearish zero: the
    owning component is marked ``insufficient_data`` and (v1) the whole score is
    returned as ``insufficient_data`` rather than guessing or renormalizing.
  - Relative volume compares today's cumulative volume to the average
    cumulative volume for the same time-of-day over prior sessions - real
    same-time-of-day baselines only, never a flat average.

``build_entry_quality_score(...)`` is a pure function (bars in, contract out,
injected clock) so every rule is unit-testable offline.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from catalystiq.schemas.entry_quality import EntryQualityComponent, EntryQualityScore
from catalystiq.schemas.market_data import IntradayBar

FORMULA_VERSION = "entry_quality_v1"

# Component -> maximum points. Must total 100 (asserted in tests).
COMPONENT_WEIGHTS: dict[str, int] = {
    "vwap_distance": 20,
    "ema9_distance": 15,
    "intraday_rsi": 15,
    "time_since_pullback": 10,
    "relative_volume": 15,
    "morning_range_extension": 15,
    "risk_reward": 10,
}

# The opening range is the first 30 minutes of the regular session.
_OPENING_RANGE_MINUTES = 30
# A pullback is a dip of at least this % off a running intraday high.
_PULLBACK_THRESHOLD_PCT = 0.5
# Intraday RSI / EMA periods (bars, on the chosen interval).
_RSI_PERIOD = 14
_EMA_FAST = 9
_EMA_SLOW = 20
# Minimum bars required for the short-term components to be meaningful.
_MIN_TODAY_BARS = 6


def _rating(score: int) -> str:
    if score >= 90:
        return "Excellent Entry"
    if score >= 80:
        return "Good Entry"
    if score >= 70:
        return "Acceptable"
    if score >= 60:
        return "Caution"
    return "Poor Entry"


def _component(
    name: str, score: int, inputs: dict, explanation: str
) -> EntryQualityComponent:
    return EntryQualityComponent(
        name=name, score=score, max_score=COMPONENT_WEIGHTS[name], status="available",
        inputs=inputs, explanation=explanation, formula_version=FORMULA_VERSION,
    )


def _insufficient(name: str, reason: str) -> EntryQualityComponent:
    return EntryQualityComponent(
        name=name, score=None, max_score=COMPONENT_WEIGHTS[name],
        status="insufficient_data", inputs={}, explanation=reason,
        formula_version=FORMULA_VERSION,
    )


# --- Intraday frame helpers -------------------------------------------------


def _to_frame(bars: list[IntradayBar]) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "timestamp": [b.timestamp for b in bars],
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
        }
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["session"] = df["timestamp"].dt.date
    return df.sort_values("timestamp").reset_index(drop=True)


def _vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    cum_pv = (typical * df["volume"]).cumsum()
    cum_v = df["volume"].cumsum().replace(0, np.nan)
    return cum_pv / cum_v


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def _rsi(closes: pd.Series, period: int = _RSI_PERIOD) -> pd.Series:
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
    rsi = np.where(avg_loss == 0, np.where(avg_gain == 0, 50.0, 100.0), rsi)
    return pd.Series(rsi, index=closes.index)


# --- Component scorers (each returns an EntryQualityComponent) ---------------


def _score_vwap_distance(today: pd.DataFrame, price: float) -> EntryQualityComponent:
    vwap = _vwap(today).iloc[-1]
    if not np.isfinite(vwap) or vwap <= 0:
        return _insufficient("vwap_distance", "No usable intraday VWAP (zero volume).")
    pct = (price - vwap) / vwap * 100.0
    # Highest at/just above VWAP or on a shallow pullback into it; penalize
    # extended-above and well-below-in-an-uptrend.
    if -0.5 <= pct <= 0.5:
        score = 20
    elif 0.5 < pct <= 1.0 or -1.0 <= pct < -0.5:
        score = 16
    elif 1.0 < pct <= 2.0:
        score = 10
    elif pct > 2.0:
        score = 4
    else:  # pct < -1.0 : trading well below VWAP in a long setup
        score = 4
    inputs = {"vwap": round(float(vwap), 4), "price": round(price, 4),
              "distance_pct": round(float(pct), 3)}
    expl = ("Distance of price from the session VWAP. Highest at or slightly "
            "above VWAP (or pulling back into it); lower when extended >2% above "
            "or trading well below.")
    return _component("vwap_distance", score, inputs, expl)


def _score_ema9_distance(today: pd.DataFrame, price: float) -> EntryQualityComponent:
    closes = today["close"]
    ema9 = _ema(closes, _EMA_FAST)
    last = ema9.iloc[-1]
    if not np.isfinite(last) or last <= 0:
        return _insufficient("ema9_distance",
                             f"Insufficient bars for a {_EMA_FAST}-period EMA.")
    pct = (price - last) / last * 100.0
    # Is the EMA rising? (compare to a few bars back within today's session)
    prior = ema9.dropna()
    rising = len(prior) >= 4 and prior.iloc[-1] > prior.iloc[-4]
    abs_pct = abs(pct)
    if abs_pct <= 0.5:
        proximity = 9
    elif abs_pct <= 1.0:
        proximity = 7
    elif abs_pct <= 2.0:
        proximity = 4
    else:
        proximity = 2
    score = proximity + (6 if rising else 0)
    inputs = {"ema9": round(float(last), 4), "price": round(price, 4),
              "distance_pct": round(float(pct), 3), "ema9_rising": bool(rising)}
    expl = ("Short-term extension vs the 9-period EMA plus whether that EMA is "
            "rising. Highest near a rising EMA; lower when extended above it.")
    return _component("ema9_distance", score, inputs, expl)


def _score_intraday_rsi(today: pd.DataFrame) -> EntryQualityComponent:
    rsi = _rsi(today["close"]).iloc[-1]
    if not np.isfinite(rsi):
        return _insufficient("intraday_rsi",
                             f"Insufficient bars for a {_RSI_PERIOD}-period RSI.")
    r = float(rsi)
    if 50 <= r <= 65:
        score = 15
    elif 65 < r <= 70:
        score = 12
    elif 70 < r <= 75:
        score = 8
    elif r > 75:
        score = 3
    elif 45 <= r < 50:
        score = 10
    elif 40 <= r < 45:
        score = 6
    else:  # r < 40 : weak momentum
        score = 3
    inputs = {"intraday_rsi": round(r, 2), "period": _RSI_PERIOD}
    expl = ("Intraday RSI. Preferred 50-65 (constructive, not exhausted); "
            "penalized above 75 (overextended) and below 40 (weak momentum).")
    return _component("intraday_rsi", score, inputs, expl)


def _score_time_since_pullback(today: pd.DataFrame, now: dt.datetime) -> EntryQualityComponent:
    """Minutes since the most recent meaningful pullback low (a dip of at least
    ``_PULLBACK_THRESHOLD_PCT`` off a running intraday high). Fresh pullbacks and
    consolidations score high; long uninterrupted rallies score low."""
    highs = today["high"].to_numpy()
    lows = today["low"].to_numpy()
    times = today["timestamp"].to_numpy()
    if len(highs) < _MIN_TODAY_BARS:
        return _insufficient("time_since_pullback",
                             "Too few intraday bars to detect a pullback.")
    running_high = highs[0]
    last_pullback_ts = None
    for i in range(1, len(highs)):
        running_high = max(running_high, highs[i])
        drawdown_pct = (running_high - lows[i]) / running_high * 100.0
        if drawdown_pct >= _PULLBACK_THRESHOLD_PCT:
            last_pullback_ts = times[i]
    now_ts = pd.Timestamp(now).tz_convert("UTC") if now.tzinfo else pd.Timestamp(now, tz="UTC")
    if last_pullback_ts is None:
        # No intraday consolidation at all -> continuous move, least attractive.
        minutes = None
        score = 2
    else:
        minutes = (now_ts - pd.Timestamp(last_pullback_ts)).total_seconds() / 60.0
        if minutes <= 30:
            score = 10
        elif minutes <= 60:
            score = 8
        elif minutes <= 90:
            score = 6
        elif minutes <= 120:
            score = 4
        else:
            score = 2
    inputs = {"minutes_since_pullback": round(minutes, 1) if minutes is not None else None,
              "pullback_threshold_pct": _PULLBACK_THRESHOLD_PCT}
    expl = ("Minutes since the last meaningful pullback (dip off a running high). "
            "Fresh pullbacks / consolidations score high; 2h+ uninterrupted "
            "rallies score low (more prone to profit-taking).")
    return _component("time_since_pullback", score, inputs, expl)


def _score_relative_volume(
    today: pd.DataFrame, priors: list[pd.DataFrame]
) -> EntryQualityComponent:
    """Today's cumulative volume vs the average cumulative volume at the SAME
    time-of-day over prior sessions. Rewards institutional participation
    (1.2-2.5x); penalizes thin liquidity, collapsing volume, and >4x exhaustion."""
    if not priors:
        return _insufficient("relative_volume",
                             "No prior-session intraday history for a time-of-day baseline.")
    cutoff = today["timestamp"].iloc[-1].time()
    today_cum = float(today["volume"].sum())
    prior_cums: list[float] = []
    for p in priors:
        upto = p[p["timestamp"].dt.time <= cutoff]
        if not upto.empty:
            prior_cums.append(float(upto["volume"].sum()))
    if not prior_cums:
        return _insufficient("relative_volume",
                             "No prior bars at this time-of-day for a baseline.")
    baseline = float(np.mean(prior_cums))
    if baseline <= 0:
        return _insufficient("relative_volume", "Zero prior-session baseline volume.")
    rv = today_cum / baseline
    if 1.2 <= rv <= 2.5:
        score = 15
    elif 1.0 <= rv < 1.2:
        score = 11
    elif 2.5 < rv <= 4.0:
        score = 9
    elif 0.7 <= rv < 1.0:
        score = 6
    elif rv > 4.0:  # excessive -> exhaustion risk
        score = 5
    else:  # rv < 0.7 : thin / collapsing liquidity
        score = 3
    inputs = {"relative_volume": round(rv, 3), "sessions_in_baseline": len(prior_cums),
              "as_of_time_utc": cutoff.strftime("%H:%M")}
    expl = ("Cumulative volume vs the same time-of-day average over prior "
            "sessions. Rewards 1.2-2.5x participation; penalizes thin volume and "
            ">4x exhaustion.")
    return _component("relative_volume", score, inputs, expl)


def _score_morning_range_extension(
    today: pd.DataFrame, price: float
) -> EntryQualityComponent:
    """Distance from the opening range (first 30 min) measured in intraday ATRs.
    Rewards a retest / controlled breakout; penalizes >2 ATRs above (chasing)."""
    start = today["timestamp"].iloc[0]
    window = today[today["timestamp"] < start + pd.Timedelta(minutes=_OPENING_RANGE_MINUTES)]
    if window.empty:
        window = today.iloc[:1]
    orh = float(window["high"].max())
    orl = float(window["low"].min())
    # Intraday ATR proxy: mean bar range across the session.
    atr = float((today["high"] - today["low"]).mean())
    if atr <= 0:
        return _insufficient("morning_range_extension", "Zero intraday range (no ATR).")
    if price < orl:
        # Broke below the opening range - a breakdown, not a chase.
        score = 5
        ext = (price - orl) / atr
    else:
        ext = (price - orh) / atr
        if ext <= 0.5:  # at/retesting the opening-range high
            score = 15
        elif ext <= 1.0:
            score = 12
        elif ext <= 2.0:
            score = 8
        else:  # >2 ATRs above -> most of the day's move is done
            score = 3
    inputs = {"opening_range_high": round(orh, 4), "opening_range_low": round(orl, 4),
              "intraday_atr": round(atr, 4), "extension_atr": round(float(ext), 3),
              "opening_range_minutes": _OPENING_RANGE_MINUTES}
    expl = ("Distance above today's opening range in intraday ATRs. Rewards a "
            "retest / controlled breakout; penalizes >2 ATRs above (chasing).")
    return _component("morning_range_extension", score, inputs, expl)


def _score_risk_reward(
    today: pd.DataFrame, price: float, prev_day_high: float | None
) -> EntryQualityComponent:
    """Reward/risk to the nearest support below price. Supports considered:
    VWAP, 9-EMA, 20-EMA, opening-range low, previous day's high, intraday swing
    low. Reward is the run to the nearest resistance above (intraday swing high)."""
    closes = today["close"]
    vwap = _vwap(today).iloc[-1]
    ema9 = _ema(closes, _EMA_FAST).iloc[-1]
    ema20 = _ema(closes, _EMA_SLOW).iloc[-1]
    start = today["timestamp"].iloc[0]
    orl_window = today[today["timestamp"] < start + pd.Timedelta(minutes=_OPENING_RANGE_MINUTES)]
    orl = float(orl_window["low"].min()) if not orl_window.empty else None
    swing_low = float(today["low"].min())
    swing_high = float(today["high"].max())

    supports = [s for s in (vwap, ema9, ema20, orl, prev_day_high, swing_low)
                if s is not None and np.isfinite(s) and s < price]
    if not supports:
        return _insufficient("risk_reward", "No support level below current price.")
    nearest_support = max(supports)
    risk = price - nearest_support
    if risk <= 0:
        return _insufficient("risk_reward", "Non-positive risk to nearest support.")
    # Reward: run to the nearest resistance above. Use today's swing high, or the
    # prior day's high if it sits above price and closer.
    resistances = [r for r in (swing_high, prev_day_high)
                   if r is not None and np.isfinite(r) and r > price]
    reward = (min(resistances) - price) if resistances else 0.0
    rr = reward / risk if risk > 0 else 0.0
    if rr > 3:
        score = 10
    elif rr >= 2:
        score = 8
    elif rr >= 1.5:
        score = 5
    else:
        score = 2
    inputs = {"nearest_support": round(float(nearest_support), 4),
              "risk": round(float(risk), 4), "reward": round(float(reward), 4),
              "reward_risk_ratio": round(float(rr), 3)}
    expl = ("Reward/risk from the nearest support below price (VWAP, EMAs, "
            "opening-range low, prior-day high, intraday swing low) to the "
            "nearest resistance above. Higher for >3:1 setups.")
    return _component("risk_reward", score, inputs, expl)


# --- Core -------------------------------------------------------------------


def build_entry_quality_score(
    symbol: str,
    intraday_bars: list[IntradayBar],
    *,
    now: dt.datetime,
    interval: str | None = None,
    prev_day_high: float | None = None,
) -> EntryQualityScore:
    """Pure, offline-testable core. ``intraday_bars`` are timestamped bars for
    the current session plus (ideally ~20) prior sessions, used to derive VWAP,
    the opening range, intraday RSI/EMA, relative-volume-by-time-of-day and the
    risk/reward. ``now`` is the calculation timestamp.

    A missing / insufficient input marks the owning component
    ``insufficient_data`` and (v1) returns the whole score as
    ``insufficient_data`` rather than fabricating or renormalizing a number."""
    calculated_at = now if now.tzinfo else now.replace(tzinfo=dt.timezone.utc)
    warnings: list[str] = []

    def _envelope(status, score, rating, components, data_as_of, reason):
        return EntryQualityScore(
            symbol=symbol.upper(), status=status, score_type="entry_quality",
            score=score, max_score=100, rating=rating,
            formula_version=FORMULA_VERSION, calculated_at=calculated_at,
            data_as_of=data_as_of, interval=interval,
            component_coverage=f"{sum(1 for c in components if c.status == 'available')}/7",
            components=components, warnings=warnings, reason=reason,
        )

    if not intraday_bars:
        return _envelope("insufficient_data", None, None, [], None,
                         "No intraday price history available.")

    df = _to_frame(intraday_bars)
    latest_session = df["session"].iloc[-1]
    today = df[df["session"] == latest_session].reset_index(drop=True)
    priors = [
        g.reset_index(drop=True)
        for _, g in df[df["session"] != latest_session].groupby("session")
    ]
    data_as_of = today["timestamp"].iloc[-1].to_pydatetime()

    if len(today) < _MIN_TODAY_BARS:
        return _envelope("insufficient_data", None, None, [], data_as_of,
                         "Too few intraday bars in the current session yet.")

    price = float(today["close"].iloc[-1])

    components = [
        _score_vwap_distance(today, price),
        _score_ema9_distance(today, price),
        _score_intraday_rsi(today),
        _score_time_since_pullback(today, now),
        _score_relative_volume(today, priors),
        _score_morning_range_extension(today, price),
        _score_risk_reward(today, price, prev_day_high),
    ]

    missing = [c.name for c in components if c.status != "available"]
    if missing:
        reason = "Insufficient intraday data for component(s): " + ", ".join(missing) + "."
        return _envelope("insufficient_data", None, None, components, data_as_of, reason)

    total = sum(c.score for c in components)
    return _envelope("available", total, _rating(total), components, data_as_of, None)


# --- Orchestrator: fetch intraday data + score (used by the endpoints) -------

_INTRADAY_INTERVAL = "5m"
_INTRADAY_DAYS = 20


def _insufficient_score(symbol: str, now: dt.datetime, reason: str) -> EntryQualityScore:
    calculated_at = now if now.tzinfo else now.replace(tzinfo=dt.timezone.utc)
    return EntryQualityScore(
        symbol=symbol.upper(), status="insufficient_data", score_type="entry_quality",
        score=None, max_score=100, rating=None, formula_version=FORMULA_VERSION,
        calculated_at=calculated_at, data_as_of=None, interval=_INTRADAY_INTERVAL,
        component_coverage="0/7", components=[], warnings=[], reason=reason,
    )


def score_entry_quality(
    symbol: str,
    provider,
    now: dt.datetime,
    *,
    prev_day_high: float | None = None,
) -> EntryQualityScore:
    """Fetch fresh intraday bars for ``symbol`` and compute its Entry Quality.

    Intraday data is an OPTIONAL provider capability: a provider without
    ``get_intraday_ohlcv``, a fetch failure, or empty data all degrade to an
    ``insufficient_data`` score (never a fabricated number), exactly like the
    Setup Strength engine degrades on missing inputs. Never raises for a data
    problem - the caller can always attach the returned score."""
    symbol = symbol.upper()
    fetch = getattr(provider, "get_intraday_ohlcv", None)
    if not callable(fetch):
        return _insufficient_score(
            symbol, now, "Intraday data is not available from the current provider."
        )
    try:
        bars = fetch(symbol, interval=_INTRADAY_INTERVAL, days=_INTRADAY_DAYS)
    except Exception:
        return _insufficient_score(
            symbol, now, "Intraday data could not be fetched for this symbol."
        )
    if not bars:
        return _insufficient_score(
            symbol, now, "No intraday bars returned for this symbol."
        )
    return build_entry_quality_score(
        symbol, bars, now=now, interval=_INTRADAY_INTERVAL, prev_day_high=prev_day_high
    )
