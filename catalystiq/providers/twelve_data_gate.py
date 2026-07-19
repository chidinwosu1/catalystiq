"""Central credit gate for Twelve Data (restricted personal-use compliance).

Every Twelve Data request in the process routes through ONE shared gate so the
plan's credit limits are enforced centrally, not per-adapter-instance:

  - a per-minute credit budget (Basic plan: 8 credits/min),
  - a per-day credit budget (Basic plan: 800 credits/day),
  - per-endpoint credit *weights* (one request can cost more than one credit),
  - an auto-shutoff latch: if the daily cap is hit, or credential/licensing
    validation fails, the provider is disabled (fails closed) until the
    condition clears (UTC day rollover for the daily cap; process restart or an
    explicit reset otherwise).

Clocks are injectable so tests are deterministic and offline. The counters are
in-process: for the single-instance deployment this is the whole app; under
multiple workers each worker holds its own gate (documented in
TWELVE_DATA_COMPLIANCE.md - move to a shared store before scaling out).
"""
from __future__ import annotations

import datetime as dt
import threading
import time
from collections import deque
from typing import Callable

from catalystiq.providers.base import ProviderError, ProviderErrorCategory

PROVIDER = "twelve_data"

# Endpoint -> credit weight. Twelve Data charges per request in credits and some
# endpoints/batch requests cost more than one; track weights so the budget is
# measured in credits, not request count. Unknown paths default to 1.
CREDIT_WEIGHTS: dict[str, int] = {
    "quote": 1,
    "time_series": 1,
    "symbol_search": 1,
    "exchanges": 1,
}

_DAILY_CAP_REASON = "daily credit limit reached"


def _default_utc_date() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


class TwelveDataGate:
    def __init__(
        self,
        credits_per_minute: int = 8,
        credits_per_day: int = 800,
        monotonic: Callable[[], float] = time.monotonic,
        utc_date: Callable[[], dt.date] = _default_utc_date,
    ) -> None:
        self.credits_per_minute = credits_per_minute
        self.credits_per_day = credits_per_day
        self._monotonic = monotonic
        self._utc_date = utc_date
        self._lock = threading.RLock()
        self._minute_events: deque[tuple[float, int]] = deque()
        self._minute_used = 0
        self._day = utc_date()
        self._day_used = 0
        self._disabled_reason: str | None = None

    # --- window maintenance ---------------------------------------------
    def _roll_minute(self, now: float) -> None:
        cutoff = now - 60.0
        while self._minute_events and self._minute_events[0][0] <= cutoff:
            _, credits = self._minute_events.popleft()
            self._minute_used -= credits

    def _roll_day(self) -> None:
        today = self._utc_date()
        if today != self._day:
            self._day = today
            self._day_used = 0
            # A new UTC day clears an auto-disable caused purely by the daily cap.
            if self._disabled_reason == _DAILY_CAP_REASON:
                self._disabled_reason = None

    # --- enforcement ----------------------------------------------------
    def charge(self, credits: int = 1) -> None:
        """Reserve `credits` before a request. Raises ProviderError (RATE_LIMITED
        or UNAVAILABLE) without consuming anything when a limit is hit or the
        provider is disabled."""
        if credits <= 0:
            credits = 1
        with self._lock:
            self._roll_day()
            if self._disabled_reason is not None:
                raise ProviderError(
                    f"Twelve Data is auto-disabled: {self._disabled_reason}.",
                    category=ProviderErrorCategory.UNAVAILABLE,
                    provider=PROVIDER,
                )
            now = self._monotonic()
            self._roll_minute(now)
            if self._minute_used + credits > self.credits_per_minute:
                raise ProviderError(
                    f"Twelve Data per-minute credit limit "
                    f"({self.credits_per_minute}) reached.",
                    category=ProviderErrorCategory.RATE_LIMITED,
                    provider=PROVIDER,
                )
            if self._day_used + credits > self.credits_per_day:
                # Daily cap -> shut the provider off for the rest of the UTC day.
                self._disabled_reason = _DAILY_CAP_REASON
                raise ProviderError(
                    f"Twelve Data daily credit limit ({self.credits_per_day}) "
                    "reached; provider disabled until UTC day rollover.",
                    category=ProviderErrorCategory.RATE_LIMITED,
                    provider=PROVIDER,
                )
            self._minute_events.append((now, credits))
            self._minute_used += credits
            self._day_used += credits

    def charge_endpoint(self, path: str) -> int:
        """Charge the credit weight for an endpoint path; returns the weight."""
        weight = CREDIT_WEIGHTS.get(path, 1)
        self.charge(weight)
        return weight

    def disable(self, reason: str) -> None:
        """Latch the provider off (credential/licensing failure). Cleared only by
        reset() (or, for the daily cap, a UTC day rollover)."""
        with self._lock:
            self._disabled_reason = reason

    def reset(self) -> None:
        with self._lock:
            self._minute_events.clear()
            self._minute_used = 0
            self._day = self._utc_date()
            self._day_used = 0
            self._disabled_reason = None

    @property
    def disabled(self) -> bool:
        with self._lock:
            self._roll_day()
            return self._disabled_reason is not None

    def status(self) -> dict:
        with self._lock:
            self._roll_day()
            self._roll_minute(self._monotonic())
            return {
                "disabled": self._disabled_reason is not None,
                "disabled_reason": self._disabled_reason,
                "credits_used_this_minute": self._minute_used,
                "credits_per_minute": self.credits_per_minute,
                "credits_used_today": self._day_used,
                "credits_per_day": self.credits_per_day,
            }


_GATE: TwelveDataGate | None = None
_GATE_LOCK = threading.Lock()


def get_twelve_data_gate() -> TwelveDataGate:
    """The process-wide singleton gate, built from settings on first use."""
    global _GATE
    with _GATE_LOCK:
        if _GATE is None:
            from catalystiq.config import get_settings

            settings = get_settings()
            per_day = settings.twelve_data_credits_per_day
            # An optional local budget can only LOWER the plan cap, never raise it.
            local = getattr(settings, "twelve_data_daily_request_budget", 0)
            if local:
                per_day = min(per_day, local)
            _GATE = TwelveDataGate(
                credits_per_minute=settings.twelve_data_credits_per_minute,
                credits_per_day=per_day,
            )
        return _GATE


def reset_twelve_data_gate() -> None:
    """Drop the singleton (tests / config reloads)."""
    global _GATE
    with _GATE_LOCK:
        _GATE = None
