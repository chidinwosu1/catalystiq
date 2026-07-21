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

from catalystiq.schemas.entry_quality import (
    EntryCheck,
    EntryQualityComponent,
    EntryQualityScore,
    EntryReason,
)
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


# --- Plain-language Entry Check verdict layer -------------------------------
# A non-technical translation of the seven component scores into the four
# answers a user actually needs: enter now or wait, what price to wait for, why,
# and where to exit. All text is TEMPLATED from validated decision reasons -
# never free-form model prose (acceptance criterion 5).

# System status -> the exact user-facing label (acceptance criterion: no
# "Buy Now"; five fixed statuses).
_USER_STATUS: dict[str, str] = {
    "favorable": "Entry Looks Favorable",
    "almost_ready": "Almost Ready — Keep Watching",
    "wait_for_pullback": "Wait for a Lower Price",
    "avoid": "Avoid This Entry for Now",
    "data_unavailable": "Cannot Evaluate Right Now",
}

# A price within this fraction of the preferred range counts as "near" it.
_NEAR_BAND = 0.003
# Preferred entry band padding around the VWAP/9-EMA anchor zone.
_ENTRY_GIVE = 0.0015
# Minimum plain-language reward:risk before we'll call an entry "favorable".
_MIN_FAVORABLE_RR = 1.5


def _money(v: float | None) -> str:
    return f"${v:.2f}" if v is not None else "—"


def _reason(key: str, state: str, label: str) -> EntryReason:
    return EntryReason(key=key, label=label, state=state)


def _data_unavailable_check(reason_text: str) -> EntryCheck:
    """A well-formed 'Cannot Evaluate Right Now' verdict so the UI always has a
    clear answer to show - never a wall of blank/zero numbers."""
    return EntryCheck(
        system_status="data_unavailable",
        user_status=_USER_STATUS["data_unavailable"],
        headline="There isn't enough live intraday data to evaluate an entry right now.",
        what_to_do="Live intraday data isn't available yet, so there's nothing to act on.",
        current_price=None, preferred_entry_low=None, preferred_entry_high=None,
        distance_to_entry_pct=None, exit_level=None, target=None,
        possible_loss_per_share=None, possible_gain_per_share=None, reward_to_risk=None,
        confirmation=False, confirmation_label="Waiting for price to start recovering",
        reasons=[], data_state="unavailable",
    )


def _component_by(components: list[EntryQualityComponent], name: str) -> EntryQualityComponent:
    for c in components:
        if c.name == name:
            return c
    raise KeyError(name)  # pragma: no cover - callers pass a complete set


def _detect_recovering(today: pd.DataFrame) -> bool:
    """Has price started to recover? A higher-low with an up-close on the last
    completed bar. Uses COMPLETED candles only (acceptance criterion 8)."""
    if len(today) < 2:
        return False
    last, prev = today.iloc[-1], today.iloc[-2]
    return bool(last["close"] > prev["close"] and last["low"] >= prev["low"])


def _build_entry_check(
    symbol: str,
    today: pd.DataFrame,
    components: list[EntryQualityComponent],
    *,
    latest_price: float | None,
    setup_is_strong: bool | None,
) -> EntryCheck:
    """Translate the (all-available) component scores into the plain-language
    verdict. Candle-based inputs come from the completed-candle component math;
    only the *current price* and its distances may reflect ``latest_price`` (a
    15s-fresh quote), so a partial candle never corrupts the scores."""
    sym = symbol.upper()
    vwap = float(_component_by(components, "vwap_distance").inputs["vwap"])
    ema9 = float(_component_by(components, "ema9_distance").inputs["ema9"])
    mre = _component_by(components, "morning_range_extension").inputs
    atr = float(mre["intraday_atr"])
    rr_in = _component_by(components, "risk_reward").inputs
    support = float(rr_in["nearest_support"])
    reward = float(rr_in["reward"])
    relvol_score = _component_by(components, "relative_volume").score or 0

    close = float(today["close"].iloc[-1])
    current = float(latest_price) if latest_price is not None else close

    # Preferred entry band: a pullback into the VWAP / 9-EMA zone.
    anchor_low, anchor_high = min(vwap, ema9), max(vwap, ema9)
    low = round(anchor_low * (1 - _ENTRY_GIVE), 2)
    high = round(anchor_high * (1 + _ENTRY_GIVE), 2)
    entry_mid = round((low + high) / 2, 2)

    exit_level = round(min(support, low) - 0.15 * atr, 2)
    target = round(close + reward, 2) if reward > 0 else None

    possible_loss = round(entry_mid - exit_level, 2) if exit_level < entry_mid else None
    possible_gain = (
        round(target - entry_mid, 2) if target is not None and target > entry_mid else None
    )
    reward_to_risk = (
        round(possible_gain / possible_loss, 1)
        if possible_loss and possible_gain and possible_loss > 0
        else None
    )

    in_range = low <= current <= high
    if current > high:
        distance = round((current - high) / high * 100, 2)
    elif current < low:
        distance = round((current - low) / low * 100, 2)
    else:
        distance = 0.0

    recovering = _detect_recovering(today)
    rr_ok = reward_to_risk is not None and reward_to_risk >= _MIN_FAVORABLE_RR

    # Status decision (deterministic).
    if current < support:
        status = "avoid"
    elif in_range:
        status = "favorable" if (recovering and rr_ok) else "almost_ready"
    elif current > high:
        status = "almost_ready" if (current - high) / high <= _NEAR_BAND else "wait_for_pullback"
    else:  # between support and the low edge of the preferred band
        status = "almost_ready"

    # Checklist reasons (plain language, four items).
    if setup_is_strong is True:
        r_setup = _reason("setup_strong", "good", "Overall stock setup is strong")
    elif setup_is_strong is False:
        r_setup = _reason("setup_strong", "bad", "Overall stock setup is weak")
    else:
        r_setup = _reason("setup_strong", "pending", "Overall stock setup not yet confirmed")

    if in_range:
        r_price = _reason("price_in_area", "good", "Current price is in the preferred entry area")
    elif current > high:
        r_price = _reason("price_in_area", "bad", "Current price is still too high")
    else:
        r_price = _reason("price_in_area", "bad", "Current price is below the preferred area")

    if relvol_score >= 9:
        r_activity = _reason("activity_healthy", "good", "Trading activity is healthy")
    elif relvol_score == 5:
        r_activity = _reason("activity_healthy", "bad", "Trading activity looks overheated")
    else:
        r_activity = _reason("activity_healthy", "bad", "Trading activity is light")

    if recovering:
        r_recover = _reason("recovering", "good", "Price has started recovering")
    else:
        r_recover = _reason("recovering", "pending", "Waiting for price to start recovering")

    reasons = [r_setup, r_price, r_activity, r_recover]

    # Templated headline + guidance per status.
    if status == "favorable":
        headline = f"{sym} is inside the preferred entry area and has started to recover."
        what = ("The price is inside the preferred entry range and has started recovering. "
                "Review the trade before making a decision.")
    elif status == "wait_for_pullback":
        headline = (
            f"{sym} has a strong overall setup, but its current price is higher than the "
            "preferred entry area." if setup_is_strong
            else f"{sym}'s current price is higher than the preferred entry area."
        )
        what = f"Wait for {sym} to move between {_money(low)} and {_money(high)}."
    elif status == "avoid":
        headline = f"{sym} has fallen below its support level."
        what = "The price has fallen below support. Wait for the setup to improve."
    else:  # almost_ready
        headline = f"{sym} is near the preferred entry area, but it has not started recovering yet."
        what = f"Keep watching for {sym} to settle into {_money(low)}–{_money(high)} and start recovering."

    return EntryCheck(
        system_status=status, user_status=_USER_STATUS[status],
        headline=headline, what_to_do=what,
        current_price=round(current, 2),
        preferred_entry_low=low, preferred_entry_high=high,
        distance_to_entry_pct=distance,
        exit_level=exit_level, target=target,
        possible_loss_per_share=possible_loss, possible_gain_per_share=possible_gain,
        reward_to_risk=reward_to_risk,
        confirmation=recovering,
        confirmation_label=("Price has started recovering" if recovering
                            else "Waiting for price to start recovering"),
        reasons=reasons, data_state="current",
    )


# --- Core -------------------------------------------------------------------


def build_entry_quality_score(
    symbol: str,
    intraday_bars: list[IntradayBar],
    *,
    now: dt.datetime,
    interval: str | None = None,
    prev_day_high: float | None = None,
    latest_price: float | None = None,
    setup_is_strong: bool | None = None,
) -> EntryQualityScore:
    """Pure, offline-testable core. ``intraday_bars`` are timestamped bars for
    the current session plus (ideally ~20) prior sessions, used to derive VWAP,
    the opening range, intraday RSI/EMA, relative-volume-by-time-of-day and the
    risk/reward. ``now`` is the calculation timestamp.

    A missing / insufficient input marks the owning component
    ``insufficient_data`` and (v1) returns the whole score as
    ``insufficient_data`` rather than fabricating or renormalizing a number.

    ``latest_price`` (an optional 15s-fresh quote) refreshes only the Entry Check
    verdict's *current price* and its distances; the seven component scores stay
    on completed candles so a partial candle never corrupts them. ``setup_is_strong``
    (the daily Setup Strength band) feeds the plain-language checklist."""
    calculated_at = now if now.tzinfo else now.replace(tzinfo=dt.timezone.utc)
    warnings: list[str] = []

    def _envelope(status, score, rating, components, data_as_of, reason, entry_check):
        return EntryQualityScore(
            symbol=symbol.upper(), status=status, score_type="entry_quality",
            score=score, max_score=100, rating=rating,
            formula_version=FORMULA_VERSION, calculated_at=calculated_at,
            data_as_of=data_as_of, interval=interval,
            component_coverage=f"{sum(1 for c in components if c.status == 'available')}/7",
            components=components, warnings=warnings, reason=reason,
            entry_check=entry_check,
        )

    if not intraday_bars:
        return _envelope("insufficient_data", None, None, [], None,
                         "No intraday price history available.",
                         _data_unavailable_check("No intraday price history available."))

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
                         "Too few intraday bars in the current session yet.",
                         _data_unavailable_check("Too few intraday bars in the current session yet."))

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
        return _envelope("insufficient_data", None, None, components, data_as_of, reason,
                         _data_unavailable_check(reason))

    total = sum(c.score for c in components)
    entry_check = _build_entry_check(
        symbol, today, components, latest_price=latest_price, setup_is_strong=setup_is_strong
    )
    return _envelope("available", total, _rating(total), components, data_as_of, None, entry_check)


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
        entry_check=_data_unavailable_check(reason),
    )


def _gated(provider, label: str, fn):
    """Run a provider call through the shared per-provider MarketDataGate
    (concurrency limit + rate-limit circuit-breaker), so the intraday Entry
    Check feed never fans out unbounded against a throttled endpoint - the same
    protection every other market-data call in this codebase already uses."""
    from catalystiq.providers.market_data_gate import get_gate_for

    return get_gate_for(provider).run(label, fn)


def _best_effort_quote(provider, symbol: str) -> float | None:
    """A 15s-fresh current price for the verdict layer. Best-effort only: any
    failure (no live feed) returns None and the score falls back to the last
    completed close - never a fabricated price. Routed through the gate."""
    getq = getattr(provider, "get_quote", None)
    if not callable(getq):
        return None
    try:
        return float(_gated(provider, f"entry-quote {symbol}", lambda: getq(symbol)).price)
    except Exception:
        return None


def score_entry_quality(
    symbol: str,
    provider,
    now: dt.datetime,
    *,
    prev_day_high: float | None = None,
    setup_is_strong: bool | None = None,
) -> EntryQualityScore:
    """Fetch fresh intraday bars for ``symbol`` and compute its Entry Quality.

    Intraday data is an OPTIONAL provider capability: a provider without
    ``get_intraday_ohlcv``, a fetch failure, or empty data all degrade to an
    ``insufficient_data`` score (never a fabricated number), exactly like the
    Setup Strength engine degrades on missing inputs. Never raises for a data
    problem - the caller can always attach the returned score. ``setup_is_strong``
    (the daily Setup Strength band) feeds the plain-language Entry Check checklist.

    The intraday fetch and the quote are routed through the shared MarketDataGate
    so 15s polling across cards can't hammer a throttled provider."""
    symbol = symbol.upper()
    fetch = getattr(provider, "get_intraday_ohlcv", None)
    if not callable(fetch):
        return _insufficient_score(
            symbol, now, "Intraday data is not available from the current provider."
        )
    try:
        bars = _gated(
            provider, f"entry-intraday {symbol}",
            lambda: fetch(symbol, interval=_INTRADAY_INTERVAL, days=_INTRADAY_DAYS),
        )
    except Exception:
        return _insufficient_score(
            symbol, now, "Intraday data could not be fetched for this symbol."
        )
    if not bars:
        return _insufficient_score(
            symbol, now, "No intraday bars returned for this symbol."
        )
    return build_entry_quality_score(
        symbol, bars, now=now, interval=_INTRADAY_INTERVAL, prev_day_high=prev_day_high,
        latest_price=_best_effort_quote(provider, symbol), setup_is_strong=setup_is_strong,
    )


# --- Short-TTL per-symbol cache ---------------------------------------------
# The 15s UI poll for a symbol is already deduped client-side, but multiple tabs
# / users / the scan can request the same symbol at once. This tiny cache
# coalesces those into one compute within a short window; a fresh compute still
# happens roughly every `ttl` seconds. Never serves across symbols and never
# stores a fabricated result.

import threading as _threading  # noqa: E402
import time as _time  # noqa: E402
from dataclasses import dataclass as _dataclass  # noqa: E402


@_dataclass
class _EntryCacheEntry:
    score: EntryQualityScore
    stored_at: float


_ENTRY_CACHE: dict[tuple, _EntryCacheEntry] = {}
_ENTRY_CACHE_LOCK = _threading.Lock()


def clear_entry_quality_cache() -> None:
    """Drop cached Entry Check results. Test-support only."""
    with _ENTRY_CACHE_LOCK:
        _ENTRY_CACHE.clear()


def score_entry_quality_cached(
    symbol: str,
    provider,
    now: dt.datetime,
    *,
    setup_is_strong: bool | None = None,
    ttl_seconds: float | None = None,
    monotonic=_time.monotonic,
) -> EntryQualityScore:
    """`score_entry_quality` with a short-TTL result cache keyed by
    (symbol, provider). Within the TTL a repeat request returns the cached score
    instead of re-fetching from the provider - so 15s polling across cards / tabs
    coalesces into one provider round-trip per window."""
    if ttl_seconds is None:
        from catalystiq.config import get_settings

        ttl_seconds = get_settings().entry_check_cache_ttl_seconds

    symbol = symbol.upper()
    key = (symbol, getattr(provider, "PROVIDER_NAME", type(provider).__name__))

    if ttl_seconds > 0:
        with _ENTRY_CACHE_LOCK:
            entry = _ENTRY_CACHE.get(key)
            if entry is not None and (monotonic() - entry.stored_at) < ttl_seconds:
                return entry.score

    score = score_entry_quality(symbol, provider, now, setup_is_strong=setup_is_strong)
    if ttl_seconds > 0:
        with _ENTRY_CACHE_LOCK:
            _ENTRY_CACHE[key] = _EntryCacheEntry(score=score, stored_at=monotonic())
    return score


def resolve_entry_quality(
    symbol: str, now: dt.datetime, *, setup_is_strong: bool | None = None
) -> EntryQualityScore:
    """Resolve the DEDICATED intraday provider (Webull real-time when configured,
    else Yahoo) and score, cached. Fully defensive: if the provider can't even be
    constructed, returns an ``insufficient_data`` score instead of raising, so a
    provider/credential problem degrades Entry Check to "Cannot Evaluate Right
    Now" rather than 500-ing the endpoint. This is the single entry point the
    router and scan use."""
    from catalystiq.providers.market_data import get_intraday_market_data_provider

    try:
        provider = get_intraday_market_data_provider()
    except Exception:
        return _insufficient_score(
            symbol, now, "Intraday market-data provider is unavailable."
        )
    return score_entry_quality_cached(symbol, provider, now, setup_is_strong=setup_is_strong)
