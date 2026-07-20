"""Dataset builder consumes the provider-neutral interface, no fabrication."""
import datetime as dt

from catalystiq.ml.dataset.builder import ExampleRequest, TrainingExampleBuilder
from catalystiq.ml.features.schema import DataQualityStatus, PointInTimeFeature
from catalystiq.ml.labels.barriers import Bar


class _StubProvider:
    """A synthetic, point-in-time-respecting provider for unit tests only."""

    def get_features(self, symbol, prediction_timestamp):
        avail = prediction_timestamp - dt.timedelta(hours=1)
        ev = prediction_timestamp - dt.timedelta(days=1)
        def f(name, value, status=DataQualityStatus.OK, provider="yahoo"):
            return PointInTimeFeature(
                symbol=symbol, prediction_timestamp=prediction_timestamp, feature_name=name,
                feature_value=value, source_provider=provider, source_event_timestamp=ev,
                available_at_timestamp=avail, retrieved_at_timestamp=prediction_timestamp,
                data_quality_status=status,
            )
        return [
            f("rsi_14", 55.0),
            f("atr_14", 2.0),
            f("estimated_spread_bps", 8.0),
            f("adv_dollar_20d", 20_000_000.0),
            f("beta_60d", None, status=DataQualityStatus.MISSING),  # a real gap
        ]

    def get_executable_entry(self, symbol, prediction_timestamp):
        return (prediction_timestamp + dt.timedelta(days=1), 100.0)

    def get_forward_path(self, symbol, entry_session, horizon_days):
        return [Bar(100, 101, 99, 100), Bar(100, 106, 99, 105)]


def test_builder_builds_example_with_labels_and_gaps():
    b = TrainingExampleBuilder(_StubProvider(), is_synthetic=True, source_providers=["yahoo"])
    ds = b.build([ExampleRequest("SYN", dt.datetime(2025, 1, 2, 20), "long", 5)])
    assert ds.size == 1
    ex = ds.examples[0]
    # executable entry used (next session), not the prediction-time price
    assert ex.entry_session > ex.prediction_timestamp
    assert ex.labels.net_terminal_return is not None
    # a genuinely missing feature is RECORDED, not fabricated
    assert "beta_60d" in ex.requirement_gaps
    assert ex.features["beta_60d__is_missing"] == 1


def test_builder_marks_dataset_synthetic_and_versions():
    b = TrainingExampleBuilder(_StubProvider(), is_synthetic=True)
    ds = b.build([ExampleRequest("SYN", dt.datetime(2025, 1, 2, 20), "long", 5)])
    assert ds.is_synthetic is True
    assert ds.training_data_version().startswith("synthetic-")


def test_builder_skips_when_no_entry():
    class NoEntry(_StubProvider):
        def get_executable_entry(self, symbol, prediction_timestamp):
            return None

    b = TrainingExampleBuilder(NoEntry())
    ds = b.build([ExampleRequest("SYN", dt.datetime(2025, 1, 2, 20), "long", 5)])
    assert ds.size == 0
    assert ds.skipped and "executable entry" in ds.skipped[0]["reason"]
