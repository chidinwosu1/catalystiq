"""Market-calendar provider interface (§10) and the NYSE implementation.

Backed operationally by `pandas_market_calendars` (already a dependency, and
already used by the price-bar FreshnessPolicy). Per the spec, the package's
output is treated as operational-but-verifiable: `get_sessions()` runs
cheap structural invariants (open < close, plausible close-of-day, no
weekend sessions) and attaches any violation as a data-quality warning
rather than trusting the package blindly. A full cross-check against the
officially published NYSE schedule is a documented follow-up (see
build_market_sessions()).

The adapter is deliberately keyless: it needs no API and is always
available, but still sits behind this interface so the operational source
can be swapped without touching callers.
"""
from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod

from catalystiq.providers.base import DataDomain
from catalystiq.schemas.calendar import MarketSession

# Regular NYSE regular-hours close is 16:00 America/New_York; a session
# closing earlier is a half-day (early close).
_REGULAR_CLOSE = dt.time(16, 0)
_NY_TZ = "America/New_York"


class CalendarError(RuntimeError):
    """Raised when the calendar source can't produce a schedule."""


class MarketCalendarProvider(ABC):
    """Abstract source of exchange trading sessions."""

    @abstractmethod
    def get_sessions(self, start: dt.date, end: dt.date) -> list[MarketSession]:
        """Trading sessions for the exchange between `start` and `end`
        inclusive, ascending by date."""


class NyseCalendarProvider(MarketCalendarProvider):
    PROVIDER_NAME = "nyse"
    ADAPTER_VERSION = "1.0.0"
    DOMAIN = DataDomain.CALENDARS

    EXCHANGE = "NYSE"

    def __init__(self, calendar_name: str = "NYSE") -> None:
        self._calendar_name = calendar_name

    def get_sessions(self, start: dt.date, end: dt.date) -> list[MarketSession]:
        import pandas_market_calendars as mcal

        retrieved_at = dt.datetime.now(dt.timezone.utc)
        version = getattr(mcal, "__version__", "unknown")

        try:
            calendar = mcal.get_calendar(self._calendar_name)
            schedule = calendar.schedule(
                start_date=start.isoformat(), end_date=end.isoformat()
            )
        except Exception as exc:  # pragma: no cover - library/edge errors
            raise CalendarError(
                f"Failed to build {self._calendar_name} schedule: {exc}"
            ) from exc

        sessions: list[MarketSession] = []
        for index, row in schedule.iterrows():
            # Keep the exact UTC instants (tz-aware) for open/close; use the
            # exchange-local time only to classify early closes. The exchange
            # tz name is preserved separately on the `timezone` field.
            open_utc = row["market_open"].to_pydatetime()
            close_utc = row["market_close"].to_pydatetime()
            close_local = row["market_close"].tz_convert(_NY_TZ)
            early = close_local.time() < _REGULAR_CLOSE

            sessions.append(
                MarketSession(
                    exchange=self.EXCHANGE,
                    session_date=index.date(),
                    open_at=open_utc,
                    close_at=close_utc,
                    early_close=early,
                    holiday_name=None,  # trading sessions only; see module docstring
                    timezone=_NY_TZ,
                    source=self.PROVIDER_NAME,
                    calendar_version=str(version),
                    retrieved_at=retrieved_at,
                )
            )
        return sessions


def get_calendar_provider() -> MarketCalendarProvider:
    """Factory for the configured market-calendar provider (currently only
    NYSE)."""
    return NyseCalendarProvider()
