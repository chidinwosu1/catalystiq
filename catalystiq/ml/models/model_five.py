"""Model 5 - Aggregate Investor Functional Response.

Backs the Investor Functional Behavior Analysis section. It analyzes AGGREGATE
market behavior only - measurable changes in price, volume, volatility,
options positioning, breadth, relative strength - and NEVER claims to predict,
diagnose or infer the psychology, intent or future actions of any individual
investor. Psychological labels ("fear", "herding") are avoided; only
operationally-defined proxies over observable data are used, and even those
are displayed as proxies ("Crowding proxy detected"), never as claims about
investors' minds.

Two governed stages:

  * Stage A - antecedent detector: which VERIFIED, timestamped antecedents are
    active at the prediction timestamp. Deterministic where possible (MA/level
    breach, abnormal gap/volume, volatility expansion, earnings/filing/macro
    timestamps). Returns nothing (``No validated antecedent detected``) when no
    verified event exists - never a plausible-sounding template trigger.

  * Stage B - conditional response model: given a verified antecedent and
    point-in-time state, estimate response-class probabilities, magnitude
    quantiles and time-to-confirmation/failure, plus antecedent-SPECIFIC
    confirmation and failure conditions. Baseline is transparent historical
    conditional frequency; a complex candidate is approved only if it
    materially improves chronological holdout performance and calibration.

Model 5 is a SEPARATE evidence source. It must not change Models 1-4 rankings,
probabilities or trading status. Any narrative is generated strictly from the
structured output via :func:`render_constrained_narrative` - never invented.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

ANTECEDENT_TAXONOMY_VERSION = "1.0.0"
RESPONSE_LABELS_VERSION = "1.0.0"
MIN_COMPARABLES = 100  # minimum comparable historical events per antecedent


class AntecedentType(str, Enum):
    TECHNICAL_LEVEL_BREACH = "technical_level_breach"
    ABNORMAL_GAP = "abnormal_gap"
    ABNORMAL_VOLUME = "abnormal_volume"
    VOLATILITY_EXPANSION = "volatility_expansion"
    EARNINGS_RELEASE = "earnings_release"
    EARNINGS_SURPRISE = "earnings_surprise"
    GUIDANCE_REVISION = "guidance_revision"
    SEC_FILING = "sec_filing"
    ANALYST_RATING_CHANGE = "analyst_rating_change"
    OPTIONS_POSITIONING_CHANGE = "options_positioning_change"
    SHORT_INTEREST_CHANGE = "short_interest_change"
    INSIDER_TRANSACTION = "insider_transaction"
    INSTITUTIONAL_OWNERSHIP_FILING = "institutional_ownership_filing"
    MACRO_RELEASE = "macro_release"
    INTEREST_RATE_ANNOUNCEMENT = "interest_rate_announcement"
    POLITICAL_REGULATORY_EVENT = "political_regulatory_event"
    SECTOR_SHOCK = "sector_shock"
    BROAD_MARKET_RISK_EVENT = "broad_market_risk_event"


class ResponseLabel(str, Enum):
    POSITIVE_FOLLOW_THROUGH = "positive_follow_through"
    NEGATIVE_FOLLOW_THROUGH = "negative_follow_through"
    IMMEDIATE_REVERSAL = "immediate_reversal"
    DELAYED_REVERSAL = "delayed_reversal"
    GAP_AND_HOLD = "gap_and_hold"
    GAP_AND_FADE = "gap_and_fade"
    BREAKOUT_AND_HOLD = "breakout_and_hold"
    BREAKOUT_FAILURE = "breakout_failure"
    BREAKDOWN_AND_HOLD = "breakdown_and_hold"
    BREAKDOWN_RECLAIM = "breakdown_reclaim"
    VOLATILITY_EXPANSION = "volatility_expansion"
    VOLATILITY_COMPRESSION = "volatility_compression"
    VOLUME_CONFIRMED_MOVE = "volume_confirmed_move"
    UNCONFIRMED_MOVE = "unconfirmed_move"
    CROWDED_POSITION_UNWIND = "crowded_position_unwind"
    MIXED_OR_UNCLEAR = "mixed_or_unclear"


# Antecedent types whose detection is DETERMINISTIC from validated OHLCV /
# technical data - these can be activated ahead of the full model, labeled
# Rule-Based. Others require a licensed/timestamped source before use.
DETERMINISTIC_ANTECEDENTS = {
    AntecedentType.TECHNICAL_LEVEL_BREACH,
    AntecedentType.ABNORMAL_GAP,
    AntecedentType.ABNORMAL_VOLUME,
    AntecedentType.VOLATILITY_EXPANSION,
}

RESPONSE_HORIZONS = ("intraday", "1d", "2d", "5d", "10d", "20d")


@dataclass(frozen=True)
class AntecedentDetection:
    type: AntecedentType
    detected_at: str  # ISO timestamp of the verified event
    description: str
    source_data: list[str]
    deterministic: bool
    direction_bias: str  # "up" | "down" | "neutral"


@dataclass(frozen=True)
class MarketStateSnapshot:
    """Provider-neutral, point-in-time observable state used by the
    deterministic detector. Missing fields simply produce fewer detections -
    never a fabricated antecedent."""

    symbol: str
    as_of: str
    close: float | None = None
    prior_close: float | None = None
    open_: float | None = None
    sma50: float | None = None
    prior_close_vs_sma50: float | None = None  # prior session close - sma50
    close_vs_sma50: float | None = None        # current close - sma50
    overnight_gap_pct: float | None = None
    relative_volume: float | None = None
    atr_percentile: float | None = None
    is_earnings_session: bool = False


def detect_antecedents(state: MarketStateSnapshot) -> list[AntecedentDetection]:
    """Stage A: deterministic detection of verified antecedents from validated
    data. Returns an empty list when none is present."""
    out: list[AntecedentDetection] = []

    # Technical level breach: SMA50 cross confirmed by relative volume.
    if (
        state.prior_close_vs_sma50 is not None
        and state.close_vs_sma50 is not None
        and state.relative_volume is not None
    ):
        crossed_down = state.prior_close_vs_sma50 >= 0 and state.close_vs_sma50 < 0
        crossed_up = state.prior_close_vs_sma50 <= 0 and state.close_vs_sma50 > 0
        if (crossed_down or crossed_up) and state.relative_volume >= 1.5:
            direction = "down" if crossed_down else "up"
            out.append(
                AntecedentDetection(
                    type=AntecedentType.TECHNICAL_LEVEL_BREACH,
                    detected_at=state.as_of,
                    description=(
                        f"Closed {'below' if crossed_down else 'above'} SMA50 on "
                        f"{state.relative_volume:.1f}x relative volume"
                    ),
                    source_data=["validated_ohlcv", "technical_engine"],
                    deterministic=True,
                    direction_bias=direction,
                )
            )

    if state.overnight_gap_pct is not None and abs(state.overnight_gap_pct) >= 3.0:
        out.append(
            AntecedentDetection(
                type=AntecedentType.ABNORMAL_GAP,
                detected_at=state.as_of,
                description=f"Abnormal overnight gap of {state.overnight_gap_pct:+.1f}%",
                source_data=["validated_ohlcv"],
                deterministic=True,
                direction_bias="up" if state.overnight_gap_pct > 0 else "down",
            )
        )

    if state.relative_volume is not None and state.relative_volume >= 2.0:
        out.append(
            AntecedentDetection(
                type=AntecedentType.ABNORMAL_VOLUME,
                detected_at=state.as_of,
                description=f"Abnormal relative volume {state.relative_volume:.1f}x the 20-day average",
                source_data=["validated_ohlcv"],
                deterministic=True,
                direction_bias="neutral",
            )
        )

    if state.atr_percentile is not None and state.atr_percentile >= 90.0:
        out.append(
            AntecedentDetection(
                type=AntecedentType.VOLATILITY_EXPANSION,
                detected_at=state.as_of,
                description=f"ATR at the {state.atr_percentile:.0f}th percentile (volatility expansion)",
                source_data=["validated_ohlcv", "risk_engine"],
                deterministic=True,
                direction_bias="neutral",
            )
        )

    return out


# Antecedent-SPECIFIC confirmation / failure conditions. Generic text across
# every antecedent is explicitly disallowed, so each is keyed by type and
# direction where relevant.
def confirmation_and_failure_conditions(det: AntecedentDetection) -> tuple[list[str], list[str]]:
    t = det.type
    down = det.direction_bias == "down"
    if t is AntecedentType.TECHNICAL_LEVEL_BREACH and down:
        return (
            ["Price remains below SMA50", "Downside volume remains above its 20-day average"],
            ["Price closes back above SMA50", "Relative volume declines and downside follow-through fails"],
        )
    if t is AntecedentType.TECHNICAL_LEVEL_BREACH:
        return (
            ["Close remains above the level with confirming volume", "Relative strength stays positive"],
            ["Close returns below the reclaimed level", "Move fades on declining volume"],
        )
    if t is AntecedentType.ABNORMAL_GAP and not down:
        return (
            ["Gap holds and relative strength remains positive", "No immediate gap-fill on rising volume"],
            ["Gap fills with increasing selling volume", "Price returns into the prior range"],
        )
    if t is AntecedentType.ABNORMAL_GAP:
        return (
            ["Gap-down holds with continued downside volume", "Failed rallies back toward the gap"],
            ["Rapid gap reclaim on strong volume", "Price recovers the prior close"],
        )
    if t is AntecedentType.ABNORMAL_VOLUME:
        return (
            ["Price direction confirms the volume surge with follow-through"],
            ["Move stalls despite elevated volume (unconfirmed move)"],
        )
    if t is AntecedentType.VOLATILITY_EXPANSION:
        return (
            ["Directional resolution follows the volatility expansion"],
            ["Volatility compresses back with no directional follow-through"],
        )
    if t is AntecedentType.OPTIONS_POSITIONING_CHANGE:
        return (
            ["Underlying price confirms the positioning direction"],
            ["Contrary catalyst produces a rapid unwind"],
        )
    if t is AntecedentType.ANALYST_RATING_CHANGE:
        return (
            ["Price and volume follow through; other evidence aligns"],
            ["Initial move fades without confirmation"],
        )
    # Default (still specific to the antecedent type name, not generic prose).
    return (
        [f"Observable follow-through consistent with a {t.value} continuation"],
        [f"Failure of {t.value} follow-through / reversal of the initial move"],
    )


@dataclass(frozen=True)
class ConditionalResponse:
    """Stage B output for one antecedent."""

    classification: ResponseLabel
    probability: float
    positive_follow_through_probability: float
    negative_follow_through_probability: float
    reversal_probability: float
    volatility_expansion_probability: float
    comparable_count: int
    median_1d_return: float | None
    median_5d_return: float | None
    median_confirmation_time_sessions: int | None
    median_reversal_time_sessions: int | None


class HistoricalFrequencyResponder:
    """Transparent baseline: empirical conditional frequencies per antecedent
    type (and direction), computed from historical comparables. This is the
    default Stage B model; a trained multiclass/quantile/survival candidate is
    approved only if it materially improves chronological holdout metrics."""

    def __init__(self, tables: dict[str, dict] | None = None) -> None:
        # tables[antecedent_type] -> {label_probs, positive, negative, reversal,
        # vol_expansion, count, median_1d, median_5d, conf_sessions, rev_sessions}
        self._tables = tables or {}

    def respond(self, det: AntecedentDetection) -> ConditionalResponse | str:
        key = det.type.value
        row = self._tables.get(key)
        if row is None or row.get("count", 0) < MIN_COMPARABLES:
            return "insufficient_evidence"
        label_probs: dict[str, float] = row["label_probs"]
        top_label = max(label_probs, key=label_probs.get)
        return ConditionalResponse(
            classification=ResponseLabel(top_label),
            probability=float(label_probs[top_label]),
            positive_follow_through_probability=float(row.get("positive", 0.0)),
            negative_follow_through_probability=float(row.get("negative", 0.0)),
            reversal_probability=float(row.get("reversal", 0.0)),
            volatility_expansion_probability=float(row.get("vol_expansion", 0.0)),
            comparable_count=int(row["count"]),
            median_1d_return=row.get("median_1d"),
            median_5d_return=row.get("median_5d"),
            median_confirmation_time_sessions=row.get("conf_sessions"),
            median_reversal_time_sessions=row.get("rev_sessions"),
        )


UI_DISCLAIMER = (
    "Aggregate response patterns are inferred from observable market data and "
    "comparable historical events. They are not claims about any individual investor."
)


def build_response_evidence(
    state: MarketStateSnapshot,
    responder: HistoricalFrequencyResponder,
    *,
    model_version: str = "aggregate_response_v1",
) -> dict:
    """Assemble the structured Model 5 evidence for a symbol.

    Returns a dict with ``status`` ``available`` and a list of detected
    antecedents (zero, one or many), or a ``no_antecedent`` status. Nothing is
    fabricated to fill a fixed number of UI cards.
    """
    detections = detect_antecedents(state)
    if not detections:
        return {
            "symbol": state.symbol,
            "status": "no_antecedent",
            "message": "No validated antecedent detected",
            "disclaimer": UI_DISCLAIMER,
            "provenance": {"model_version": model_version, "data_as_of": state.as_of},
        }

    antecedents = []
    for det in detections:
        resp = responder.respond(det)
        conf, fail = confirmation_and_failure_conditions(det)
        block: dict = {
            "antecedent": {
                "type": det.type.value,
                "detected_at": det.detected_at,
                "description": det.description,
                "source_data": det.source_data,
                "deterministic": det.deterministic,
            },
            "confirmation_conditions": conf,
            "failure_or_reversal_conditions": fail,
        }
        if isinstance(resp, str):
            block["aggregate_response"] = {"status": resp}
            block["reliability"] = {"score": 0, "label": "insufficient",
                                    "reasons": ["Insufficient comparable historical events"]}
        else:
            block["aggregate_response"] = {
                "classification": resp.classification.value,
                "probability": resp.probability,
                "positive_follow_through_probability": resp.positive_follow_through_probability,
                "negative_follow_through_probability": resp.negative_follow_through_probability,
                "reversal_probability": resp.reversal_probability,
                "volatility_expansion_probability": resp.volatility_expansion_probability,
            }
            block["historical_comparables"] = {
                "count": resp.comparable_count,
                "median_1d_return": resp.median_1d_return,
                "median_5d_return": resp.median_5d_return,
                "median_confirmation_time_sessions": resp.median_confirmation_time_sessions,
                "median_reversal_time_sessions": resp.median_reversal_time_sessions,
            }
        antecedents.append(block)

    return {
        "symbol": state.symbol,
        "status": "available",
        "disclaimer": UI_DISCLAIMER,
        "antecedents": antecedents,
        "provenance": {"model_version": model_version, "data_as_of": state.as_of},
    }


def render_constrained_narrative(evidence: dict) -> str | None:
    """Render a short narrative STRICTLY from structured Model 5 output using a
    constrained template. Adds nothing: no new antecedent, no changed
    probability/horizon, no causal or psychological claims. Returns ``None``
    when structured output is unavailable (no narrative should be generated).
    """
    status = evidence.get("status")
    if status == "no_antecedent":
        return "No validated antecedent detected for this symbol."
    if status != "available":
        return None
    parts: list[str] = []
    for block in evidence.get("antecedents", []):
        ant = block["antecedent"]
        resp = block.get("aggregate_response", {})
        if resp.get("status") == "insufficient_evidence":
            parts.append(
                f"{ant['description']}: insufficient comparable history to estimate an aggregate response."
            )
            continue
        cls = resp.get("classification")
        prob = resp.get("probability")
        comps = block.get("historical_comparables", {}).get("count")
        line = (
            f"{ant['description']}. Historically-estimated aggregate response: "
            f"{cls} (~{prob:.0%} of {comps} comparable events)."
        )
        parts.append(line)
    parts.append(UI_DISCLAIMER)
    return " ".join(parts)
