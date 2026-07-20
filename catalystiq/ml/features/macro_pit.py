"""Point-in-time BLS / BEA macro features (vintage read, fail-closed).

Unlike SEC filings, the macro data as currently ingested does NOT carry a
legitimate historical vintage, so these features FAIL CLOSED (emit MISSING with
a recorded reason) rather than use a possibly-revised current value:

  * **BLS** has no vintage/realtime concept - ``realtime_start/end`` are None
    and a later revision overwrites the same (series, date) row in place. There
    is therefore no way to know what value was public on a past prediction
    date. The vintage read below requires ``realtime_start <= as_of``; with
    null realtime bounds it selects nothing, so BLS features are MISSING until
    a vintage-preserving ingestion lands. The read is written correctly, so it
    lights up automatically once real vintages exist.

  * **BEA** values are stored current-state only (no realtime dimension in the
    Silver key, ``effective_at`` is None, ``source_available_at`` is our
    ingest time). A legitimate historical release cannot be established, so BEA
    features fail closed unconditionally - we never read the stored (possibly
    revised) value.

This is the required behavior: never the latest revised value, never a silent
substitution, never fabrication - and never blocking the rest of the dataset.

Provenance uses ``source_provider='bls'`` / ``'bea'`` (public macro, licensed
for use); when a value is unavailable the timestamps floor to the prediction
timestamp so the schema's ordering invariant always holds.
"""
from __future__ import annotations

import datetime as dt

from catalystiq.ml.features.schema import DataQualityStatus, PointInTimeFeature

BLS_PROVIDER = "bls"
BEA_PROVIDER = "bea"

# Default CPI-U (all items, NSA) series used for the macro CPI YoY feature.
DEFAULT_CPI_SERIES = "CUUR0000SA0"

# Recorded fail-closed reasons (surfaced in docs / report; the feature itself
# just carries data_quality_status=MISSING).
REASON_BLS_NO_VINTAGE = (
    "BLS observations carry no realtime vintage (realtime_start is null); a "
    "legitimate as-of value cannot be established from the current ingestion."
)
REASON_BEA_NO_VINTAGE = (
    "BEA values are stored current-state only (no realtime vintage); a "
    "legitimate historical release cannot be established, so the value is not used."
)


def _to_dt(d, ref: dt.datetime) -> dt.datetime:
    return dt.datetime.combine(d, dt.time(), tzinfo=ref.tzinfo)


def _missing(symbol, prediction_timestamp, retrieved_at, name, provider) -> PointInTimeFeature:
    return PointInTimeFeature(
        symbol=symbol.upper(),
        prediction_timestamp=prediction_timestamp,
        feature_name=name,
        feature_value=None,
        source_provider=provider,
        source_event_timestamp=prediction_timestamp,
        available_at_timestamp=prediction_timestamp,
        retrieved_at_timestamp=retrieved_at,
        data_quality_status=DataQualityStatus.MISSING,
    )


def pit_macro_features(
    db,
    symbol: str,
    prediction_timestamp: dt.datetime,
    *,
    as_of: dt.date,
    retrieved_at: dt.datetime,
    cpi_series: str = DEFAULT_CPI_SERIES,
) -> list[PointInTimeFeature]:
    """Return [macro_cpi_yoy_pit (BLS), macro_gdp_qoq_pit (BEA)] with strict
    point-in-time semantics; MISSING when a legitimate vintage is unavailable."""
    cpi = _cpi_yoy_feature(db, symbol, prediction_timestamp, as_of, retrieved_at, cpi_series)
    # BEA: fail closed unconditionally under current (vintage-less) ingestion.
    gdp = _missing(symbol, prediction_timestamp, retrieved_at, "macro_gdp_qoq_pit", BEA_PROVIDER)
    return [cpi, gdp]


def _cpi_yoy_feature(db, symbol, prediction_timestamp, as_of, retrieved_at, cpi_series):
    name = "macro_cpi_yoy_pit"
    if db is None:
        return _missing(symbol, prediction_timestamp, retrieved_at, name, BLS_PROVIDER)

    from catalystiq.pipelines.macro_pipeline import get_silver_observations

    # Vintage read: only rows whose realtime window contains `as_of`. With
    # BLS's null realtime bounds this yields nothing -> fail closed.
    obs = [
        o for o in get_silver_observations(db, cpi_series, provider=BLS_PROVIDER, as_of=as_of)
        if o.value is not None and o.realtime_start is not None
    ]
    if len(obs) < 13:
        return _missing(symbol, prediction_timestamp, retrieved_at, name, BLS_PROVIDER)

    obs.sort(key=lambda o: o.observation_date)
    current = obs[-1]
    target_prev = current.observation_date - dt.timedelta(days=365)
    prior = min(
        (o for o in obs if abs((o.observation_date - target_prev).days) <= 20),
        key=lambda o: abs((o.observation_date - target_prev).days),
        default=None,
    )
    if prior is None or not prior.value:
        return _missing(symbol, prediction_timestamp, retrieved_at, name, BLS_PROVIDER)

    yoy = (current.value - prior.value) / abs(prior.value)
    available = _to_dt(current.realtime_start, prediction_timestamp)
    event = _to_dt(current.observation_date, prediction_timestamp)
    if available > prediction_timestamp:
        return _missing(symbol, prediction_timestamp, retrieved_at, name, BLS_PROVIDER)
    if event > available:
        event = available
    return PointInTimeFeature(
        symbol=symbol.upper(),
        prediction_timestamp=prediction_timestamp,
        feature_name=name,
        feature_value=float(yoy),
        source_provider=BLS_PROVIDER,
        source_event_timestamp=event,
        available_at_timestamp=available,
        retrieved_at_timestamp=retrieved_at,
        data_quality_status=DataQualityStatus.OK,
    )
