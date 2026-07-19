"""Macro-data provider interface (§7, §9).

This module holds the shared MacroDataProvider ABC that persisted macro
adapters implement (BLS today). FRED is deliberately NOT here: for compliance
(no storage, no AI/ML use, kill-switchable) it lives in the isolated
`catalystiq.fred` package as an ephemeral, allowlisted reader that never
touches the Bronze/Silver/Gold layers. See FRED_COMPLIANCE.md.
"""
from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod

from catalystiq.schemas.macro import MacroObservation, MacroSeries


class MacroDataProvider(ABC):
    @abstractmethod
    def get_series(self, series_id: str) -> MacroSeries: ...

    @abstractmethod
    def get_observations(
        self,
        series_id: str,
        observation_start: dt.date | None = None,
        observation_end: dt.date | None = None,
        as_of: dt.date | None = None,
    ) -> list[MacroObservation]: ...
