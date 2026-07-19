"""Provider-neutral, point-in-time feature interface.

The ML foundation deliberately does NOT call Yahoo, Twelve Data, SEC EDGAR,
BLS/BEA or any other integration directly, and it does not modify those
adapters. Instead it consumes this thin, abstract interface. A concrete
implementation (wired in a later, separately-approved phase) is responsible
for honouring point-in-time semantics - returning only data whose
``available_at_timestamp`` is at or before the requested
``prediction_timestamp`` - and for stamping full provenance onto every
:class:`~catalystiq.ml.features.schema.PointInTimeFeature`.

Because no such production implementation is wired yet, the dataset builder
records every feature it would need in a machine-readable *requirement
manifest* (see :mod:`catalystiq.ml.features.manifest`) rather than
fabricating a value.
"""
from __future__ import annotations

import datetime as dt
from typing import Protocol, runtime_checkable

from catalystiq.ml.features.schema import PointInTimeFeature
from catalystiq.ml.labels.barriers import Bar


@runtime_checkable
class PointInTimeFeatureProvider(Protocol):
    """A source of look-ahead-free features and price paths.

    Implementations MUST NOT return any datum whose availability postdates
    ``prediction_timestamp``. The feature schema re-validates this on the way
    in (defense in depth), but the contract lives here.
    """

    def get_features(
        self, symbol: str, prediction_timestamp: dt.datetime
    ) -> list[PointInTimeFeature]:
        """Return the point-in-time feature set for ``symbol`` as known at
        ``prediction_timestamp``. Missing inputs are represented as features
        with ``data_quality_status = MISSING`` (never omitted silently)."""

    def get_executable_entry(
        self, symbol: str, prediction_timestamp: dt.datetime
    ) -> tuple[dt.datetime, float] | None:
        """Return ``(entry_session, executable_entry_price)`` - the next
        session's opening price after ``prediction_timestamp`` - or ``None``
        if it is not yet knowable/available."""

    def get_forward_path(
        self, symbol: str, entry_session: dt.datetime, horizon_days: int
    ) -> list[Bar]:
        """Return the ordered OHLC bars from ``entry_session`` forward across
        ``horizon_days`` sessions. Used ONLY for offline label generation,
        never at inference time."""
