"""Point-in-time feature provider: look-ahead invariance + provenance.

Uses an injected bars_loader so no DB/network is touched. Synthetic bars are
permitted for unit tests only.
"""
import datetime as dt
import math

import pytest

from catalystiq.schemas.market_data import OHLCVBar
from catalystiq.ml.features.pit_provider import SilverPointInTimeProvider
from catalystiq.ml.features.schema import DataQualityStatus, validate_feature
from catalystiq.ml.dataset.builder import ExampleRequest, TrainingExampleBuilder


def _series(seed=1.0, n=400, drift=0.0004, vol=0.0008):
    bars = []
    base = dt.date(2020, 1, 1)
    p = 100.0 * seed
    for i in range(n):
        p *= 1 + drift + vol * math.sin(i / 6)
        bars.append(OHLCVBar(date=base + dt.timedelta(days=i), open=p * 0.995,
                             high=p * 1.012, low=p * 0.985, close=p, volume=1_000_000 + i * 500))
    return bars


def _provider(data: dict, **kw):
    return SilverPointInTimeProvider(
        db=None, benchmark_symbol="SPY",
        sector_resolver=kw.pop("sector_resolver", lambda s: None),
        bars_loader=lambda s, db: data.get(s.upper(), []),
        **kw,
    )


PT = dt.datetime(2020, 10, 1, 20, 0, 0)


def test_features_are_point_in_time_and_provenanced():
    data = {"AAPL": _series(), "SPY": _series(seed=4.0)}
    prov = _provider(data)
    feats = prov.get_features("AAPL", PT)
    by = {f.feature_name: f for f in feats}
    assert by["rsi_14"].feature_value is not None
    # every feature is licensing/leakage-clean under the schema
    for f in feats:
        assert validate_feature(f, for_training=True) is None
        assert f.available_at_timestamp <= f.prediction_timestamp


def test_look_ahead_invariance():
    """Features as-of PT must be identical whether or not future bars exist."""
    short = {"AAPL": _series(n=400), "SPY": _series(seed=4.0, n=400)}
    long = {"AAPL": _series(n=520), "SPY": _series(seed=4.0, n=520)}
    a = {f.feature_name: f.feature_value for f in _provider(short).get_features("AAPL", PT)}
    b = {f.feature_name: f.feature_value for f in _provider(long).get_features("AAPL", PT)}
    assert set(a) == set(b)
    for k in a:
        va, vb = a[k], b[k]
        if isinstance(va, float) and isinstance(vb, float):
            assert abs(va - vb) < 1e-9, k
        else:
            assert va == vb, k


def test_unavailable_groups_recorded_missing_not_fabricated():
    prov = _provider({"AAPL": _series(), "SPY": _series(seed=4.0)})
    by = {f.feature_name: f for f in prov.get_features("AAPL", PT)}
    for name in ("trading_days_to_earnings", "pit_revenue_yoy",
                 "macro_cpi_yoy_pit", "recent_filing_event"):
        assert by[name].feature_value is None
        assert by[name].data_quality_status is DataQualityStatus.MISSING


def test_executable_entry_is_next_session_open():
    data = {"AAPL": _series(), "SPY": _series(seed=4.0)}
    prov = _provider(data)
    entry = prov.get_executable_entry("AAPL", PT)
    assert entry is not None
    entry_session, price = entry
    # entry session is strictly after the last closed session used for features
    assert entry_session.date() > PT.date() - dt.timedelta(days=1)
    # price equals that bar's open (not a price known at prediction time)
    match = [b for b in data["AAPL"] if b.date == entry_session.date()][0]
    assert price == match.open


def test_executable_entry_none_when_no_future_bar():
    # Prediction after the last available bar -> no next session -> None.
    data = {"AAPL": _series(n=100)}
    prov = _provider(data)
    late = dt.datetime(2021, 6, 1, 20, 0, 0)
    assert prov.get_executable_entry("AAPL", late) is None


def test_forward_path_length_and_order():
    data = {"AAPL": _series(), "SPY": _series(seed=4.0)}
    prov = _provider(data)
    entry = prov.get_executable_entry("AAPL", PT)
    path = prov.get_forward_path("AAPL", entry[0], 5)
    assert len(path) == 5
    sessions = [b.session for b in path]
    assert sessions == sorted(sessions)


def test_rule_based_score_populated_with_full_context():
    # Provide sector bars so the market/sector factor is available and the
    # rule-based total becomes 'available' (not insufficient_data).
    data = {"AAPL": _series(), "SPY": _series(seed=4.0), "XLK": _series(seed=2.0)}
    prov = _provider(data, sector_resolver=lambda s: "XLK")
    by = {f.feature_name: f.feature_value for f in prov.get_features("AAPL", PT)}
    assert by["rule_based_setup_strength"] is not None
    assert 0 <= by["rule_based_setup_strength"] <= 100


def test_builder_integration_produces_labeled_examples():
    data = {"AAPL": _series(n=420), "SPY": _series(seed=4.0, n=420)}
    prov = _provider(data)
    builder = TrainingExampleBuilder(prov, is_synthetic=True, source_providers=["computed"])
    reqs = [ExampleRequest("AAPL", dt.datetime(2020, 6, 1, 20) + dt.timedelta(days=7 * i), "long", 5)
            for i in range(10)]
    ds = builder.build(reqs)
    assert ds.size > 0
    ex = ds.examples[0]
    assert ex.entry_session > ex.prediction_timestamp
    assert ex.labels.net_terminal_return is not None
    # unavailable feature groups surface as recorded requirement gaps
    assert "trading_days_to_earnings" in ex.requirement_gaps


def test_manifest_reflects_wired_price_sources():
    from catalystiq.ml.features.manifest import manifest_dict

    m = manifest_dict()
    assert m["counts_by_status"].get("wired", 0) >= 20
    # rule-based + price groups are wired now
    wired = {r["feature_name"] for r in m["requirements"] if r["source_status"] == "wired"}
    assert "rsi_14" in wired and "rule_based_setup_strength" in wired


def test_support_resistance_distances_from_levels():
    """Nearest active support-below / resistance-above, as positive fractions."""
    from types import SimpleNamespace
    from catalystiq.ml.features.pit_provider import _support_resistance_distances

    def lvl(price, type_, status="active"):
        return SimpleNamespace(price=price, type=type_, status=status)

    struct = SimpleNamespace(support_resistance_levels=[
        lvl(90.0, "support"), lvl(95.0, "support"), lvl(80.0, "support"),
        lvl(110.0, "resistance"), lvl(105.0, "resistance"),
        lvl(102.0, "resistance", status="broken"),  # ignored (broken)
    ])
    ds, dr = _support_resistance_distances(struct, 100.0)
    assert abs(ds - (100.0 - 95.0) / 100.0) < 1e-9   # nearest support below
    assert abs(dr - (105.0 - 100.0) / 100.0) < 1e-9  # nearest active resistance above


def test_support_resistance_missing_when_no_active_level():
    from types import SimpleNamespace
    from catalystiq.ml.features.pit_provider import _support_resistance_distances

    struct = SimpleNamespace(support_resistance_levels=[])
    ds, dr = _support_resistance_distances(struct, 100.0)
    assert ds is None and dr is None
