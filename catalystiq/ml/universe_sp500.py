"""Build an S&P 500 training universe file for the validation runner.

Writes a newline-delimited symbols file (one ticker per line) that
``python -m catalystiq.ml.train_cli --symbols-file <path>`` can consume. Tickers
are normalized to the market-data provider's convention (e.g. ``BRK.B`` ->
``BRK-B``).

**Survivorship-bias warning (read this).** The constituent list comes from the
*current* S&P 500 membership. It therefore contains only companies that
survived and remain in the index today — it excludes every firm that was
dropped, delisted, acquired or went bankrupt. Training and validating on a
current-membership universe is **survivorship-biased and optimistic**: the
losers are missing, so metrics look better than live trading would. A genuinely
unbiased universe needs point-in-time index membership plus delisted names from
a licensed dataset (CRSP / Norgate / Sharadar / Polygon). This file is the best
*free* approximation and is clearly labeled as biased; the runner repeats the
warning in its report and MLflow tags.

Network use: fetching the live list reads the public Wikipedia
"List of S&P 500 companies" page. If that fetch is unavailable, a small,
sector-diverse FALLBACK subset is written instead (also current-membership, so
the same bias applies) and the file records which source was used.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from html.parser import HTMLParser

WIKIPEDIA_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

SURVIVORSHIP_BIAS_WARNING = (
    "SURVIVORSHIP BIAS: current-membership universe only. Delisted/dropped/"
    "bankrupt names are ABSENT, so results are optimistic (an upper bound, not "
    "live performance). Unbiased validation needs point-in-time membership + "
    "delisted names from a licensed dataset."
)

# Sector-diverse fallback used only when the live list can't be fetched. Still
# current-membership large caps, so the same survivorship caveat applies.
FALLBACK_SYMBOLS: tuple[str, ...] = (
    # Info tech
    "AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "AMD", "CSCO",
    # Communication services
    "GOOGL", "META", "NFLX", "DIS", "T", "VZ",
    # Consumer discretionary
    "AMZN", "TSLA", "HD", "MCD", "NKE", "LOW",
    # Consumer staples
    "PG", "KO", "PEP", "WMT", "COST",
    # Financials
    "JPM", "BAC", "WFC", "GS", "V", "MA",
    # Health care
    "UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV",
    # Industrials
    "CAT", "BA", "HON", "UPS", "GE",
    # Energy
    "XOM", "CVX", "COP",
    # Utilities / materials / real estate
    "NEE", "DUK", "LIN", "SHW", "PLD", "AMT",
)


def normalize_ticker(symbol: str) -> str:
    """Normalize a raw ticker to the provider convention: upper-cased, trimmed,
    with class dots turned into dashes (``BRK.B`` -> ``BRK-B``)."""
    if symbol is None:
        return ""
    s = symbol.strip().upper()
    if not s or s.startswith("#"):
        return ""
    return s.replace(".", "-")


def read_symbols_file(path: str) -> list[str]:
    """Read a symbols file, ignoring blank lines and ``#`` comments."""
    out: list[str] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.append(line)
    return out


class _TableExtractor(HTMLParser):
    """Minimal stdlib HTML table extractor (no lxml/pandas dependency).

    Collects every ``<table>`` as a list of rows, each a list of cell texts,
    so the S&P 500 constituents table can be located by its ``Symbol`` header.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None
        elif tag == "tr" and self._row is not None:
            self._table.append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._cell is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_sp500_html(html: str) -> list[str]:
    """Extract the ticker column from the Wikipedia S&P 500 table HTML."""
    extractor = _TableExtractor()
    extractor.feed(html)
    for table in extractor.tables:
        if not table:
            continue
        header = [c.strip().lower() for c in table[0]]
        col = next((i for i, h in enumerate(header) if h in ("symbol", "ticker symbol", "ticker")), None)
        if col is None:
            continue
        syms = [normalize_ticker(row[col]) for row in table[1:] if len(row) > col]
        syms = [s for s in syms if s]
        if syms:
            return syms
    raise ValueError("no symbol column found in the S&P 500 page")


def fetch_sp500_symbols(*, url: str = WIKIPEDIA_SP500_URL, timeout: float = 30.0) -> list[str]:
    """Fetch the current S&P 500 constituents from Wikipedia (network)."""
    import httpx

    headers = {"User-Agent": "CatalystIQ-universe-builder/1.0 (research; contact via repo)"}
    resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return parse_sp500_html(resp.text)


def build_universe(
    out_path: str,
    *,
    allow_fetch: bool = True,
    fetcher=None,
    now: dt.datetime | None = None,
) -> tuple[list[str], str]:
    """Write the universe file and return ``(symbols, source)``.

    ``source`` is ``"wikipedia"`` or ``"fallback"``. ``fetcher`` (for tests) is a
    zero-arg callable returning raw symbols; when omitted the live fetch is used
    if ``allow_fetch`` is true.
    """
    source = "fallback"
    symbols: list[str] = []
    if allow_fetch or fetcher is not None:
        try:
            raw = fetcher() if fetcher is not None else fetch_sp500_symbols()
            symbols = [s for s in (normalize_ticker(x) for x in raw) if s]
            if symbols:
                source = "wikipedia"
        except Exception:
            symbols = []
    if not symbols:
        symbols = list(FALLBACK_SYMBOLS)
        source = "fallback"

    # De-duplicate, preserve order.
    seen: set[str] = set()
    ordered = [s for s in symbols if not (s in seen or seen.add(s))]

    stamp = (now or dt.datetime(1970, 1, 1)).date().isoformat()
    header = [
        "# Catalyst IQ training universe (S&P 500).",
        f"# source={source}  count={len(ordered)}  generated_utc_date={stamp}",
        "#",
        "# " + SURVIVORSHIP_BIAS_WARNING,
        "#",
        "# One ticker per line; '#' comments and blank lines are ignored by",
        "# `python -m catalystiq.ml.train_cli --symbols-file`.",
    ]
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(header) + "\n")
        fh.write("\n".join(ordered) + "\n")
    return ordered, source


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="catalystiq.ml.universe_sp500",
        description="Generate an S&P 500 training universe file (survivorship-biased; see header).",
    )
    p.add_argument("--out", default="sp500_universe.txt", help="Output file path.")
    p.add_argument("--no-fetch", action="store_true",
                   help="Skip the network fetch and write the sector-diverse fallback subset.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    symbols, source = build_universe(args.out, allow_fetch=not args.no_fetch)
    print(f"wrote {len(symbols)} symbols to {args.out} (source={source})")
    print(SURVIVORSHIP_BIAS_WARNING)
    if source == "fallback":
        print("NOTE: used the offline fallback subset (live S&P 500 fetch unavailable).")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
