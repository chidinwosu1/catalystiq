"""Governed, static symbol -> sector reference data for the scan universe.

The opportunity scan must NOT make a live per-symbol fundamentals call just to
learn a symbol's sector - that per-symbol Yahoo `.info` fetch, multiplied across
the 24-symbol universe, is exactly what tripped Yahoo's per-IP rate limit (see
NVDA_RATE_LIMIT_DIAGNOSIS.md).

For the curated, controlled scan universe a symbol's sector is a stable, known
fact, so it is recorded here as governed reference data rather than fetched. The
sector *names* deliberately match the keys of
:data:`catalystiq.analysis.market_context.SECTOR_ETF_MAP` (Yahoo-style GICS
naming) so resolution to a sector ETF is a pure dict lookup.

A symbol not covered here has **no governed sector**: callers must treat the
sector as unavailable (and let the market/sector factor degrade to
insufficient_data) rather than invent one. This module never guesses.
"""
from __future__ import annotations

from catalystiq.analysis.market_context import SECTOR_ETF_MAP

# Governed sector membership for the curated scan universe (and common
# benchmarks). Names match SECTOR_ETF_MAP keys exactly. Extend deliberately
# alongside SCAN_UNIVERSE; a missing entry means "unknown", never a default.
SYMBOL_SECTOR: dict[str, str] = {
    # Technology
    "AAPL": "Technology",
    "MSFT": "Technology",
    "NVDA": "Technology",
    "AVGO": "Technology",
    # Communication Services
    "GOOGL": "Communication Services",
    "META": "Communication Services",
    # Consumer Cyclical
    "AMZN": "Consumer Cyclical",
    "TSLA": "Consumer Cyclical",
    "HD": "Consumer Cyclical",
    # Financial Services
    "JPM": "Financial Services",
    "V": "Financial Services",
    "MA": "Financial Services",
    "BAC": "Financial Services",
    # Healthcare
    "UNH": "Healthcare",
    "JNJ": "Healthcare",
    "LLY": "Healthcare",
    "ABBV": "Healthcare",
    # Energy
    "XOM": "Energy",
    "CVX": "Energy",
    # Consumer Defensive
    "WMT": "Consumer Defensive",
    "COST": "Consumer Defensive",
    "PG": "Consumer Defensive",
    "KO": "Consumer Defensive",
    "PEP": "Consumer Defensive",
}


def governed_sector(symbol: str) -> str | None:
    """The governed sector name for ``symbol``, or None if not covered."""
    return SYMBOL_SECTOR.get(symbol.upper())


def governed_sector_etf(symbol: str) -> str | None:
    """The sector ETF ticker for ``symbol`` via governed data, or None if the
    symbol has no governed sector (never a guessed default)."""
    sector = governed_sector(symbol)
    return SECTOR_ETF_MAP.get(sector) if sector else None
