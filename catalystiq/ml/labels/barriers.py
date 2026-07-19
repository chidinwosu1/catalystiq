"""Triple-barrier and path-risk outcome computation.

Given an executable entry price, a predetermined target and stop (both fixed
using information available at the prediction timestamp), and the OHLC path
over the holding period, this computes:

  * ``target_before_stop`` - did price reach the target before the stop?
  * ``stop_breach`` - was the stop reached or passed at any point?
  * ``gap_beyond_stop`` - did the next executable price gap *through* the stop
    (so the realistic fill is worse than the stop level)?
  * ``max_adverse_excursion`` / ``max_favorable_excursion`` - direction-aware,
    as signed direction-adjusted returns.
  * ``terminal_return`` - direction-adjusted return from entry to horizon exit.

Conservatism is the guiding principle for ambiguity. When a single candle
touches BOTH the target and the stop, lower-frequency (e.g. daily) data
cannot tell which came first. We NEVER assume the target came first: the
caller chooses to either EXCLUDE the example from the barrier target or COUNT
THE STOP as first. Both are safe; assuming the favorable outcome is not.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence


class BothTouchedPolicy(str, Enum):
    EXCLUDE = "exclude"        # drop the example from the barrier target
    STOP_FIRST = "stop_first"  # conservatively count the stop as first


class _Dir(str, Enum):
    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True)
class Bar:
    """A single OHLC bar on the holding path. ``session`` is an opaque,
    orderable session key (date/index) used only for provenance."""

    open: float
    high: float
    low: float
    close: float
    session: object | None = None


@dataclass(frozen=True)
class BarrierOutcome:
    # None => excluded from the barrier target (ambiguous both-touch under
    # EXCLUDE policy). Never silently set to the favorable outcome.
    target_before_stop: bool | None
    stop_breach: bool
    gap_beyond_stop: bool
    max_adverse_excursion: float
    max_favorable_excursion: float
    terminal_return: float
    # First session at which the target / stop was hit (provenance only).
    hit_session: object | None
    excluded_reason: str | None = None
    both_touched: bool = False


def _dir_adj_return(direction: _Dir, price: float, entry: float) -> float:
    if entry <= 0:
        return 0.0
    if direction is _Dir.LONG:
        return (price - entry) / entry
    return (entry - price) / entry


def compute_barrier_outcome(
    *,
    direction: str,
    entry_price: float,
    target_price: float,
    stop_price: float,
    path: Sequence[Bar],
    both_touched_policy: BothTouchedPolicy = BothTouchedPolicy.STOP_FIRST,
) -> BarrierOutcome:
    """Compute the barrier/path outcome for one trade.

    ``path`` must be the ordered sequence of bars over the holding period,
    starting at the entry session and running to the horizon. ``entry_price``
    is the *executable* entry (e.g. next session's open) - never a price that
    was already required to compute the prediction.
    """
    d = _Dir(direction)
    if not path:
        return BarrierOutcome(
            target_before_stop=None,
            stop_breach=False,
            gap_beyond_stop=False,
            max_adverse_excursion=0.0,
            max_favorable_excursion=0.0,
            terminal_return=0.0,
            hit_session=None,
            excluded_reason="empty_path",
        )

    # Validate barrier geometry relative to direction. A malformed target/stop
    # (e.g. long target below entry) is excluded rather than silently scored.
    geometry_ok = (
        (d is _Dir.LONG and target_price > entry_price > stop_price)
        or (d is _Dir.SHORT and target_price < entry_price < stop_price)
    )

    mae = 0.0
    mfe = 0.0
    stop_breach = False
    gap_beyond_stop = False
    target_before_stop: bool | None = None
    both_touched = False
    hit_session: object | None = None
    excluded_reason: str | None = None
    decided = False

    for bar in path:
        # Direction-aware favorable/adverse extremes.
        if d is _Dir.LONG:
            adverse_price, favorable_price = bar.low, bar.high
            target_touched = bar.high >= target_price
            stop_touched = bar.low <= stop_price
            gap_through = bar.open < stop_price
        else:
            adverse_price, favorable_price = bar.high, bar.low
            target_touched = bar.low <= target_price
            stop_touched = bar.high >= stop_price
            gap_through = bar.open > stop_price

        mae = min(mae, _dir_adj_return(d, adverse_price, entry_price))
        mfe = max(mfe, _dir_adj_return(d, favorable_price, entry_price))

        if stop_touched and not stop_breach:
            stop_breach = True
            # Gap-beyond-stop: the bar that first breaches the stop opened
            # already beyond it, so the realistic fill is worse than the stop.
            if gap_through:
                gap_beyond_stop = True

        if decided or not geometry_ok:
            continue

        if target_touched and stop_touched:
            both_touched = True
            hit_session = bar.session
            if both_touched_policy is BothTouchedPolicy.EXCLUDE:
                target_before_stop = None
                excluded_reason = "both_barriers_touched_same_bar"
            else:  # STOP_FIRST - never assume the target came first
                target_before_stop = False
            decided = True
        elif target_touched:
            target_before_stop = True
            hit_session = bar.session
            decided = True
        elif stop_touched:
            target_before_stop = False
            hit_session = bar.session
            decided = True

    # Neither barrier hit within the horizon: target_before_stop stays False
    # (the target was not reached first). If geometry was invalid, exclude.
    if not geometry_ok:
        target_before_stop = None
        excluded_reason = excluded_reason or "invalid_barrier_geometry"
    elif not decided:
        target_before_stop = False

    terminal_return = _dir_adj_return(d, path[-1].close, entry_price)

    return BarrierOutcome(
        target_before_stop=target_before_stop,
        stop_breach=stop_breach,
        gap_beyond_stop=gap_beyond_stop,
        max_adverse_excursion=mae,
        max_favorable_excursion=mfe,
        terminal_return=terminal_return,
        hit_session=hit_session,
        excluded_reason=excluded_reason,
        both_touched=both_touched,
    )
