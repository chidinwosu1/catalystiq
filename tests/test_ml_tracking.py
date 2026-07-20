"""Deterministic tests for the MLflow tracking wrapper.

Cover the in-memory recorder (used everywhere else to assert structure) and the
real :class:`MlflowTracker` with a MOCKED mlflow module, so no live MLflow is
required. Also cover URI/experiment resolution and the graceful fallback.
"""
import math
import sys
import types

from catalystiq.config import Settings
from catalystiq.ml.tracking import (
    MlflowTracker,
    RecordingTracker,
    flatten_metrics,
    get_tracker,
    resolve_experiment_name,
    resolve_tracking_uri,
)


def test_flatten_metrics_drops_non_finite_and_bools():
    flat = flatten_metrics({
        "roc_auc": 0.8,
        "nested": {"pinball": {"q10": 0.1, "q50": float("nan")}},
        "flag": True,
        "text": "hello",
        "none": None,
        "inf": float("inf"),
    })
    assert flat["roc_auc"] == 0.8
    assert flat["nested.pinball.q10"] == 0.1
    assert "nested.pinball.q50" not in flat  # NaN dropped
    assert "flag" not in flat and "text" not in flat and "none" not in flat
    assert "inf" not in flat


def test_recording_tracker_parent_child_structure():
    tr = RecordingTracker()
    with tr.run("parent"):
        tr.log_params({"a": 1})
        with tr.run("child_a", nested=True):
            tr.log_metrics({"m": 0.5})
            with tr.run("grandchild", nested=True):
                tr.set_tags({"t": "x"})
        with tr.run("child_b", nested=True):
            tr.log_metrics({"m": 0.7})

    assert len(tr.parent_runs) == 1
    parent = tr.parent_runs[0]
    assert parent.name == "parent" and parent.params["a"] == "1"
    children = tr.children_of(parent)
    assert {c.name for c in children} == {"child_a", "child_b"}
    child_a = tr.runs_by_name("child_a")[0]
    grand = tr.children_of(child_a)
    assert len(grand) == 1 and grand[0].name == "grandchild"
    assert grand[0].tags["t"] == "x"


def test_recording_tracker_writes_artifacts_to_disk(tmp_path):
    tr = RecordingTracker(output_dir=str(tmp_path))
    with tr.run("parent"):
        tr.log_dict({"k": 1}, "reports/data.json")
        tr.log_text("hello", "notes.txt")
    assert (tmp_path / "reports" / "data.json").exists()
    assert (tmp_path / "notes.txt").read_text() == "hello"


def test_param_value_is_truncated():
    tr = RecordingTracker()
    with tr.run("p"):
        tr.log_params({"long": "x" * 1000})
    assert len(tr.parent_runs[0].params["long"]) <= 490 + 3


def test_resolve_tracking_uri_precedence(monkeypatch):
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    # explicit setting wins
    s = Settings(action_api_key="k", mlflow_tracking_uri="http://example.invalid:5000")
    assert resolve_tracking_uri(s) == "http://example.invalid:5000"
    # env var next
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "sqlite:///x.db")
    assert resolve_tracking_uri(Settings(action_api_key="k")) == "sqlite:///x.db"
    # local mlruns fallback (no setting, no env)
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    uri = resolve_tracking_uri(Settings(action_api_key="k", mlflow_local_dir="mlruns"))
    assert uri.startswith("file:") and uri.endswith("mlruns")


def test_resolve_experiment_name(monkeypatch):
    monkeypatch.delenv("MLFLOW_EXPERIMENT_NAME", raising=False)
    assert resolve_experiment_name(Settings(action_api_key="k", mlflow_experiment_name="foo")) == "foo"


def test_get_tracker_falls_back_when_mlflow_absent(monkeypatch, tmp_path):
    import catalystiq.ml.tracking as tracking
    monkeypatch.setattr(tracking, "mlflow_available", lambda: False)
    tr = get_tracker(Settings(action_api_key="k"), fallback_dir=str(tmp_path))
    assert isinstance(tr, RecordingTracker)
    assert tr.available is False


class _FakeRunInfo:
    def __init__(self, run_id):
        self.run_id = run_id


class _FakeActiveRun:
    def __init__(self, run_id):
        self.info = _FakeRunInfo(run_id)


class _FakeMlflow(types.ModuleType):
    """Minimal mlflow stand-in capturing calls for assertions."""

    def __init__(self):
        super().__init__("mlflow")
        self.tracking_uri = None
        self.experiment = None
        self.started = []  # (name, nested)
        self.ended = 0
        self.metrics = {}
        self.params = {}
        self.tags = {}
        self.dicts = []
        self._counter = 0

    def set_tracking_uri(self, uri):
        self.tracking_uri = uri

    def set_experiment(self, name):
        self.experiment = name

    def start_run(self, run_name=None, nested=False):
        self._counter += 1
        self.started.append((run_name, nested))
        return _FakeActiveRun(f"fake-{self._counter}")

    def end_run(self):
        self.ended += 1

    def log_params(self, params):
        self.params.update(params)

    def log_metrics(self, metrics, step=None):
        self.metrics.update(metrics)

    def set_tags(self, tags):
        self.tags.update(tags)

    def log_dict(self, obj, artifact_file):
        self.dicts.append(artifact_file)

    def log_text(self, text, artifact_file):
        pass

    def log_figure(self, fig, artifact_file):
        pass


def test_mlflow_tracker_with_mocked_mlflow(monkeypatch):
    fake = _FakeMlflow()
    monkeypatch.setitem(sys.modules, "mlflow", fake)
    tracker = MlflowTracker(Settings(action_api_key="k", mlflow_tracking_uri="sqlite:///x.db",
                                     mlflow_experiment_name="exp1"))
    assert tracker.available is True
    assert fake.tracking_uri == "sqlite:///x.db" and fake.experiment == "exp1"
    with tracker.run("parent"):
        tracker.log_params({"a": 1, "skip": None})
        tracker.log_metrics({"roc": 0.9, "bad": float("nan")})
        with tracker.run("child", nested=True):
            tracker.set_tags({"t": "v"})
            tracker.log_dict({"x": 1}, "d.json")
    # parent + child started, both ended, nested flag propagated
    assert fake.started == [("parent", False), ("child", True)]
    assert fake.ended == 2
    assert fake.params == {"a": "1"}  # None skipped, coerced to str
    assert fake.metrics == {"roc": 0.9}  # NaN dropped
    assert fake.tags == {"t": "v"}
    assert fake.dicts == ["d.json"]
