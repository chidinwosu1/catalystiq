"""Tests for the S&P 500 universe builder and symbols-file loading.

Offline and deterministic: the live Wikipedia fetch is replaced with an
injected fetcher / seeded HTML, so no network is used.
"""
import argparse

import pytest

from catalystiq.ml.universe_sp500 import (
    FALLBACK_SYMBOLS,
    SURVIVORSHIP_BIAS_WARNING,
    build_universe,
    normalize_ticker,
    parse_sp500_html,
    read_symbols_file,
)


def test_normalize_ticker():
    assert normalize_ticker(" brk.b ") == "BRK-B"
    assert normalize_ticker("AAPL") == "AAPL"
    assert normalize_ticker("bf.b") == "BF-B"
    assert normalize_ticker("# comment") == ""
    assert normalize_ticker("") == ""
    assert normalize_ticker(None) == ""


def test_build_universe_from_fetcher_dedupes_and_normalizes(tmp_path):
    out = tmp_path / "u.txt"
    syms, source = build_universe(str(out), fetcher=lambda: ["AAPL", "MSFT", "BRK.B", "aapl", "  "])
    assert source == "wikipedia"
    assert syms == ["AAPL", "MSFT", "BRK-B"]  # deduped, normalized, order preserved
    # header carries the survivorship-bias warning; file is round-trippable
    text = out.read_text()
    assert "SURVIVORSHIP BIAS" in text
    assert read_symbols_file(str(out)) == ["AAPL", "MSFT", "BRK-B"]


def test_build_universe_falls_back_when_fetch_fails(tmp_path):
    out = tmp_path / "f.txt"

    def _boom():
        raise RuntimeError("network down")

    syms, source = build_universe(str(out), fetcher=_boom)
    assert source == "fallback"
    assert syms == list(FALLBACK_SYMBOLS)
    assert "SURVIVORSHIP BIAS" in out.read_text()


def test_build_universe_no_fetch_uses_fallback(tmp_path):
    out = tmp_path / "n.txt"
    syms, source = build_universe(str(out), allow_fetch=False)
    assert source == "fallback" and syms == list(FALLBACK_SYMBOLS)


def test_read_symbols_file_ignores_comments_and_blanks(tmp_path):
    p = tmp_path / "s.txt"
    p.write_text("# header\n\nAAPL\n  MSFT \n# note\nSPY\n")
    assert read_symbols_file(str(p)) == ["AAPL", "MSFT", "SPY"]


def test_parse_sp500_html():
    html = """
    <table class="wikitable">
      <tr><th>Symbol</th><th>Security</th></tr>
      <tr><td>AAPL</td><td>Apple</td></tr>
      <tr><td>BRK.B</td><td>Berkshire</td></tr>
    </table>
    """
    syms = parse_sp500_html(html)
    assert syms == ["AAPL", "BRK-B"]


def test_warning_text_is_explicit():
    assert "SURVIVORSHIP BIAS" in SURVIVORSHIP_BIAS_WARNING
    assert "delisted" in SURVIVORSHIP_BIAS_WARNING.lower()


# --- CLI symbols loading ----------------------------------------------------
def _args(symbols=None, symbols_file=None):
    return argparse.Namespace(symbols=symbols, symbols_file=symbols_file)


def test_cli_load_symbols_from_file_and_flag_combined(tmp_path):
    from catalystiq.ml.train_cli import _load_symbols

    f = tmp_path / "u.txt"
    f.write_text("# hdr\nMSFT\nbrk.b\nAAPL\n")
    got = _load_symbols(_args(symbols="AAPL,NVDA", symbols_file=str(f)))
    # --symbols first, then file; deduped + normalized, order preserved
    assert got == ["AAPL", "NVDA", "MSFT", "BRK-B"]


def test_cli_load_symbols_empty_raises():
    from catalystiq.ml.train_cli import _load_symbols

    with pytest.raises(SystemExit):
        _load_symbols(_args(symbols=None, symbols_file=None))
