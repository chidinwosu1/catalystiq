"""MLflow experiment-tracking wrapper for the offline training/validation run.

This is a thin, defensively-imported layer over MLflow. It exists so the
training/evaluation orchestrator (:mod:`catalystiq.ml.experiment`) can log a
parent run for the whole experiment and nested child runs for each model,
horizon, fold and candidate algorithm WITHOUT importing MLflow at module load
time and without any hard-coded tracking URL or credential.

Two implementations share one interface (:class:`BaseTracker`):

  * :class:`MlflowTracker` - records to a real MLflow backend. The tracking URI
    is resolved, in order, from the explicit ``MLFLOW_TRACKING_URI`` setting,
    the environment variable of the same name (which MLflow also reads
    natively), and finally a local ``mlruns`` directory for development. No URL
    or credential is ever hard-coded here; a remote server's auth is supplied
    through MLflow's own environment variables.

  * :class:`RecordingTracker` - an in-memory recorder that also writes artifacts
    to a local directory. It is used by the deterministic tests (to assert the
    parent/child run structure and the logged params/metrics/artifacts without
    a live MLflow), and as a graceful fallback when MLflow is not installed so
    the runner still completes and still emits its reports and plots on disk.

Use :func:`get_tracker` to obtain the right one for the current environment.

Nothing in this module enables training, inference, serving or model approval;
it only records what an already-authorized offline run computes.
"""
from __future__ import annotations

import json
import math
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping

from catalystiq.config import Settings


# --------------------------------------------------------------------------
# metric / param helpers (shared)
# --------------------------------------------------------------------------
def flatten_metrics(obj: Any, prefix: str = "") -> dict[str, float]:
    """Flatten an arbitrarily-nested metric structure into ``dotted.key -> float``.

    Only finite numeric leaves survive; ``None``, NaN, inf, strings and bools
    are dropped (a bool is metadata, not a metric). This lets the caller pass a
    whole metric bundle (e.g. the nested ``quantile_metrics`` result) straight
    to :meth:`BaseTracker.log_metrics`.
    """
    out: dict[str, float] = {}

    def _walk(node: Any, key: str) -> None:
        if isinstance(node, Mapping):
            for k, v in node.items():
                _walk(v, f"{key}.{k}" if key else str(k))
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                _walk(v, f"{key}.{i}" if key else str(i))
        elif isinstance(node, bool):
            return  # a bool is a flag, not a metric
        elif isinstance(node, (int, float)):
            val = float(node)
            if math.isfinite(val):
                out[key] = val

    _walk(obj, prefix)
    return out


def _param_value(value: Any) -> str:
    """Coerce a param to a short string (MLflow stores params as strings)."""
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, default=str, sort_keys=True)
    else:
        text = str(value)
    return text if len(text) <= 490 else text[:487] + "..."


# --------------------------------------------------------------------------
# run handle
# --------------------------------------------------------------------------
@dataclass
class RunHandle:
    """Lightweight reference to an active run (real or recorded)."""

    name: str
    run_id: str
    nested: bool = False


# --------------------------------------------------------------------------
# base interface
# --------------------------------------------------------------------------
class BaseTracker:
    backend: str = "base"

    @property
    def available(self) -> bool:
        return False

    @contextmanager
    def run(self, name: str, *, nested: bool = False) -> Iterator[RunHandle]:  # pragma: no cover - overridden
        raise NotImplementedError

    def log_params(self, params: Mapping[str, Any]) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def log_metrics(self, metrics: Mapping[str, Any], *, prefix: str = "", step: int | None = None) -> None:  # pragma: no cover
        raise NotImplementedError

    def set_tags(self, tags: Mapping[str, Any]) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def log_dict(self, obj: Any, artifact_file: str) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def log_text(self, text: str, artifact_file: str) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def log_figure(self, fig: Any, artifact_file: str) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


# --------------------------------------------------------------------------
# real MLflow backend
# --------------------------------------------------------------------------
def resolve_tracking_uri(settings: Settings | None) -> str:
    """Resolve the tracking URI without ever hard-coding one.

    Order of precedence: explicit ``mlflow_tracking_uri`` setting, the
    ``MLFLOW_TRACKING_URI`` environment variable, then a local ``mlruns``
    directory (a ``file:`` URI) for development.
    """
    if settings is not None:
        configured = (getattr(settings, "mlflow_tracking_uri", "") or "").strip()
        if configured:
            return configured
    env_uri = (os.environ.get("MLFLOW_TRACKING_URI") or "").strip()
    if env_uri:
        return env_uri
    local_dir = "mlruns"
    if settings is not None:
        local_dir = (getattr(settings, "mlflow_local_dir", "") or "mlruns").strip() or "mlruns"
    return "file:" + os.path.abspath(local_dir)


def resolve_experiment_name(settings: Settings | None) -> str:
    if settings is not None:
        name = (getattr(settings, "mlflow_experiment_name", "") or "").strip()
        if name:
            return name
    return (os.environ.get("MLFLOW_EXPERIMENT_NAME") or "").strip() or "catalystiq-ml-validation"


class MlflowTracker(BaseTracker):
    """Records to a real MLflow tracking backend (imported lazily)."""

    backend = "mlflow"

    def __init__(self, settings: Settings | None = None) -> None:
        import mlflow  # lazy: only imported when actually tracking to MLflow

        self._mlflow = mlflow
        self._tracking_uri = resolve_tracking_uri(settings)
        self._experiment_name = resolve_experiment_name(settings)
        # Recent MLflow puts the local filesystem store in "maintenance mode"
        # and refuses it unless the operator opts in. A local ``mlruns`` dir is
        # exactly the documented development backend, so opt in for that case
        # (never overriding an explicit choice). Remote/DB backends are
        # untouched. The dashboard may need the same env var: see the runner
        # docs (``MLFLOW_ALLOW_FILE_STORE=true mlflow ui``).
        if self._tracking_uri.startswith("file:") or "://" not in self._tracking_uri:
            os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
        mlflow.set_tracking_uri(self._tracking_uri)
        mlflow.set_experiment(self._experiment_name)

    @property
    def available(self) -> bool:
        return True

    @property
    def tracking_uri(self) -> str:
        return self._tracking_uri

    @property
    def experiment_name(self) -> str:
        return self._experiment_name

    @contextmanager
    def run(self, name: str, *, nested: bool = False) -> Iterator[RunHandle]:
        active = self._mlflow.start_run(run_name=name, nested=nested)
        try:
            yield RunHandle(name=name, run_id=active.info.run_id, nested=nested)
        finally:
            self._mlflow.end_run()

    def log_params(self, params: Mapping[str, Any]) -> None:
        clean = {k: _param_value(v) for k, v in params.items() if v is not None}
        if clean:
            self._mlflow.log_params(clean)

    def log_metrics(self, metrics: Mapping[str, Any], *, prefix: str = "", step: int | None = None) -> None:
        flat = flatten_metrics(metrics, prefix)
        if flat:
            self._mlflow.log_metrics(flat, step=step)

    def set_tags(self, tags: Mapping[str, Any]) -> None:
        clean = {k: _param_value(v) for k, v in tags.items() if v is not None}
        if clean:
            self._mlflow.set_tags(clean)

    def log_dict(self, obj: Any, artifact_file: str) -> None:
        self._mlflow.log_dict(_jsonable(obj), artifact_file)

    def log_text(self, text: str, artifact_file: str) -> None:
        self._mlflow.log_text(text, artifact_file)

    def log_figure(self, fig: Any, artifact_file: str) -> None:
        self._mlflow.log_figure(fig, artifact_file)


# --------------------------------------------------------------------------
# in-memory / on-disk recording backend (tests + graceful fallback)
# --------------------------------------------------------------------------
@dataclass
class RecordedRun:
    name: str
    run_id: str
    nested: bool
    parent_id: str | None
    params: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)


class RecordingTracker(BaseTracker):
    """Records the full run tree in memory (and, optionally, artifacts to disk).

    Used by the deterministic tests to assert parent/child structure and logged
    values without a live MLflow, and as the graceful fallback when MLflow is
    not installed - in which case ``output_dir`` is set so the operator still
    gets the JSON reports and plots on disk.
    """

    backend = "recording"

    def __init__(self, output_dir: str | None = None) -> None:
        self.runs: list[RecordedRun] = []
        self._stack: list[RecordedRun] = []
        self._counter = 0
        self.output_dir = output_dir

    @property
    def available(self) -> bool:
        return False

    # -- structure helpers used by tests ---------------------------------
    def runs_by_name(self, name: str) -> list[RecordedRun]:
        return [r for r in self.runs if r.name == name]

    def children_of(self, run: RecordedRun) -> list[RecordedRun]:
        return [r for r in self.runs if r.parent_id == run.run_id]

    @property
    def parent_runs(self) -> list[RecordedRun]:
        return [r for r in self.runs if r.parent_id is None]

    def _current(self) -> RecordedRun | None:
        return self._stack[-1] if self._stack else None

    @contextmanager
    def run(self, name: str, *, nested: bool = False) -> Iterator[RunHandle]:
        self._counter += 1
        run_id = f"rec-{self._counter:04d}"
        parent = self._current()
        record = RecordedRun(
            name=name, run_id=run_id, nested=nested,
            parent_id=parent.run_id if (nested and parent is not None) else None,
        )
        self.runs.append(record)
        self._stack.append(record)
        try:
            yield RunHandle(name=name, run_id=run_id, nested=nested)
        finally:
            self._stack.pop()

    def _target(self) -> RecordedRun:
        cur = self._current()
        if cur is None:
            # Logging outside any run: attach to a synthetic root record so the
            # value is not silently lost.
            cur = RecordedRun(name="<root>", run_id="rec-root", nested=False, parent_id=None)
            self.runs.append(cur)
            self._stack.append(cur)
        return cur

    def log_params(self, params: Mapping[str, Any]) -> None:
        tgt = self._target()
        for k, v in params.items():
            if v is not None:
                tgt.params[k] = _param_value(v)

    def log_metrics(self, metrics: Mapping[str, Any], *, prefix: str = "", step: int | None = None) -> None:
        tgt = self._target()
        tgt.metrics.update(flatten_metrics(metrics, prefix))

    def set_tags(self, tags: Mapping[str, Any]) -> None:
        tgt = self._target()
        for k, v in tags.items():
            if v is not None:
                tgt.tags[k] = _param_value(v)

    def log_dict(self, obj: Any, artifact_file: str) -> None:
        self._target().artifacts.append(artifact_file)
        self._write_artifact(artifact_file, lambda p: _dump_json(_jsonable(obj), p))

    def log_text(self, text: str, artifact_file: str) -> None:
        self._target().artifacts.append(artifact_file)
        self._write_artifact(artifact_file, lambda p: _dump_text(text, p))

    def log_figure(self, fig: Any, artifact_file: str) -> None:
        self._target().artifacts.append(artifact_file)
        self._write_artifact(artifact_file, lambda p: fig.savefig(p))

    def _write_artifact(self, artifact_file: str, writer) -> None:
        if not self.output_dir:
            return
        path = os.path.join(self.output_dir, artifact_file)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        writer(path)


# --------------------------------------------------------------------------
# factory + small json helpers
# --------------------------------------------------------------------------
def mlflow_available() -> bool:
    try:
        import mlflow  # noqa: F401
        return True
    except Exception:
        return False


def get_tracker(settings: Settings | None = None, *, fallback_dir: str | None = None) -> BaseTracker:
    """Return an :class:`MlflowTracker` if MLflow is importable, else a
    :class:`RecordingTracker` that writes artifacts under ``fallback_dir`` so
    the runner degrades gracefully instead of failing when MLflow is absent."""
    if mlflow_available():
        try:
            return MlflowTracker(settings)
        except Exception:  # pragma: no cover - environment dependent
            pass
    return RecordingTracker(output_dir=fallback_dir or "ml_runner_output")


def _jsonable(obj: Any) -> Any:
    """Best-effort conversion of dataclasses / sets / etc. into JSON types."""
    return json.loads(json.dumps(obj, default=_default))


def _default(o: Any) -> Any:
    from dataclasses import asdict, is_dataclass

    if is_dataclass(o):
        return asdict(o)
    if isinstance(o, set):
        return sorted(o)
    return str(o)


def _dump_json(obj: Any, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=str)


def _dump_text(text: str, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
