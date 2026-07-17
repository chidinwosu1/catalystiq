"""TA-Lib-backed reference calculations for the 8 indicators where
Catalyst IQ's own implementation and TA-Lib compute the same, standard,
named indicator: SMA, RSI, MACD, ATR, OBV, Bollinger Bands, the
Accumulation/Distribution line, and the Money Flow Index.

Each function returns a `ReferenceSeries` (values + TA-Lib's own warm-up
"lookback" count for that exact parameterization, via `talib.abstract`) so
the comparator can exclude each indicator's own warm-up window rather than
a single hardcoded number.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import talib
from talib import abstract


@dataclass(frozen=True)
class ReferenceSeries:
    values: np.ndarray
    lookback: int


def _lookback(name: str, **params) -> int:
    fn = abstract.Function(name)
    fn.set_parameters(**params)
    return fn.lookback


def sma(close: np.ndarray, timeperiod: int) -> ReferenceSeries:
    return ReferenceSeries(
        values=talib.SMA(close, timeperiod=timeperiod),
        lookback=_lookback("SMA", timeperiod=timeperiod),
    )


def rsi(close: np.ndarray, timeperiod: int) -> ReferenceSeries:
    return ReferenceSeries(
        values=talib.RSI(close, timeperiod=timeperiod),
        lookback=_lookback("RSI", timeperiod=timeperiod),
    )


def macd(
    close: np.ndarray, fastperiod: int, slowperiod: int, signalperiod: int
) -> tuple[ReferenceSeries, ReferenceSeries, ReferenceSeries]:
    macd_line, signal_line, hist = talib.MACD(
        close, fastperiod=fastperiod, slowperiod=slowperiod, signalperiod=signalperiod
    )
    lb = _lookback("MACD", fastperiod=fastperiod, slowperiod=slowperiod, signalperiod=signalperiod)
    return (
        ReferenceSeries(macd_line, lb),
        ReferenceSeries(signal_line, lb),
        ReferenceSeries(hist, lb),
    )


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, timeperiod: int) -> ReferenceSeries:
    return ReferenceSeries(
        values=talib.ATR(high, low, close, timeperiod=timeperiod),
        lookback=_lookback("ATR", timeperiod=timeperiod),
    )


def obv(close: np.ndarray, volume: np.ndarray) -> ReferenceSeries:
    return ReferenceSeries(values=talib.OBV(close, volume), lookback=0)


def bbands(
    close: np.ndarray, timeperiod: int, nbdevup: float, nbdevdn: float
) -> tuple[ReferenceSeries, ReferenceSeries, ReferenceSeries]:
    upper, middle, lower = talib.BBANDS(
        close, timeperiod=timeperiod, nbdevup=nbdevup, nbdevdn=nbdevdn
    )
    lb = _lookback("BBANDS", timeperiod=timeperiod, nbdevup=nbdevup, nbdevdn=nbdevdn)
    return ReferenceSeries(upper, lb), ReferenceSeries(middle, lb), ReferenceSeries(lower, lb)


def ad(high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray) -> ReferenceSeries:
    return ReferenceSeries(values=talib.AD(high, low, close, volume), lookback=0)


def mfi(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, timeperiod: int
) -> ReferenceSeries:
    return ReferenceSeries(
        values=talib.MFI(high, low, close, volume, timeperiod=timeperiod),
        lookback=_lookback("MFI", timeperiod=timeperiod),
    )
