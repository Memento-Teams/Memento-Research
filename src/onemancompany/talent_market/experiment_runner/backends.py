"""Metric backends for local-first experiment monitoring."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Protocol

from onemancompany.talent_market.experiment_runner.models import (
    ExperimentConfig,
    MetricPoint,
    MetricSnapshot,
)


class MetricsBackend(Protocol):
    """Common interface for experiment metric sources."""

    name: str

    def detect(self) -> bool: ...

    def read(self) -> MetricSnapshot: ...


class JsonlMetricsBackend:
    """Zero-dependency backend for newline-delimited JSON metrics."""

    name = "jsonl"

    def __init__(self, config: ExperimentConfig) -> None:
        self.config = config
        self.path: Path | None = None

    def detect(self) -> bool:
        self.path = _find_jsonl_file(self.config)
        return self.path is not None

    def read(self) -> MetricSnapshot:
        path = self.path or _find_jsonl_file(self.config)
        self.path = path
        if path is None:
            return MetricSnapshot(source=self.name, error="No metrics.jsonl file found.")

        points: list[MetricPoint] = []
        try:
            for line in _tail_lines(path, max_lines=2000):
                line = line.strip()
                if not line:
                    continue
                raw = json.loads(line)
                point = _point_from_json(raw)
                if point.values:
                    points.append(point)
        except Exception as exc:
            return MetricSnapshot(source=self.name, error=f"Failed to read {path}: {exc}")
        return MetricSnapshot(source=self.name, points=points)


class TensorBoardMetricsBackend:
    """TensorBoard event-file backend with optional dependency import."""

    name = "tensorboard"

    def __init__(self, config: ExperimentConfig) -> None:
        self.config = config
        self.event_file: Path | None = None

    def detect(self) -> bool:
        self.event_file = _find_tensorboard_event_file(self.config)
        return self.event_file is not None

    def read(self) -> MetricSnapshot:
        event_file = self.event_file or _find_tensorboard_event_file(self.config)
        self.event_file = event_file
        if event_file is None:
            return MetricSnapshot(source=self.name, error="No TensorBoard event file found.")

        try:
            from tensorboard.backend.event_processing.event_accumulator import EventAccumulator
        except Exception:
            return MetricSnapshot(
                source=self.name,
                error="TensorBoard event file found, but optional package 'tensorboard' is not installed.",
            )

        try:
            accumulator = EventAccumulator(str(event_file.parent))
            accumulator.Reload()
            tags = accumulator.Tags().get("scalars", [])
            if not tags:
                return MetricSnapshot(source=self.name, error="TensorBoard file has no scalar tags.")

            target = self.config.target_metric
            selected = [target] if target and target in tags else tags
            points_by_step: dict[float, MetricPoint] = {}
            for tag in selected:
                for scalar in accumulator.Scalars(tag):
                    point = points_by_step.setdefault(
                        float(scalar.step),
                        MetricPoint(values={}, step=float(scalar.step), timestamp=float(scalar.wall_time)),
                    )
                    point.values[tag] = float(scalar.value)
            points = sorted(points_by_step.values(), key=lambda p: (p.step is None, p.step or 0))
            return MetricSnapshot(source=self.name, points=points)
        except Exception as exc:
            return MetricSnapshot(source=self.name, error=f"Failed to read TensorBoard events: {exc}")


class WandbMetricsBackend:
    """Optional W&B backend. Used only when task config explicitly points to a run."""

    name = "wandb"

    def __init__(self, config: ExperimentConfig) -> None:
        self.config = config

    def detect(self) -> bool:
        return bool(self.config.wandb_project and self.config.wandb_run_id)

    def read(self) -> MetricSnapshot:
        if not self.detect():
            return MetricSnapshot(source=self.name, error="wandb_project and wandb_run_id are required.")
        try:
            import wandb
        except Exception:
            return MetricSnapshot(source=self.name, error="Optional package 'wandb' is not installed.")

        try:
            api = wandb.Api()
            run = api.run(f"{self.config.wandb_project}/{self.config.wandb_run_id}")
            keys = [self.config.target_metric] if self.config.target_metric else None
            history = run.history(keys=keys)
            points: list[MetricPoint] = []
            for _, row in history.iterrows():
                values = {
                    str(k): float(v)
                    for k, v in row.items()
                    if k != "_step" and _is_number(v)
                }
                if values:
                    points.append(MetricPoint(values=values, step=_optional_float(row.get("_step"))))
            return MetricSnapshot(source=self.name, points=points)
        except Exception as exc:
            return MetricSnapshot(source=self.name, error=f"Failed to read W&B run: {exc}")


class AutoMetricsBackend:
    """Select JSONL, TensorBoard, then W&B in local-first order."""

    name = "auto"

    def __init__(self, config: ExperimentConfig) -> None:
        self.config = config
        self.backend: MetricsBackend | None = None
        requested = config.monitor_backend.lower()
        if requested == "jsonl":
            self.candidates: list[MetricsBackend] = [JsonlMetricsBackend(config)]
        elif requested == "tensorboard":
            self.candidates = [TensorBoardMetricsBackend(config)]
        elif requested == "wandb":
            self.candidates = [WandbMetricsBackend(config)]
        else:
            self.candidates = [
                JsonlMetricsBackend(config),
                TensorBoardMetricsBackend(config),
                WandbMetricsBackend(config),
            ]

    def detect(self) -> bool:
        for backend in self.candidates:
            if backend.detect():
                self.backend = backend
                self.name = backend.name
                return True
        return False

    def read(self) -> MetricSnapshot:
        if self.backend is None:
            self.detect()
        if self.backend is not None:
            return self.backend.read()

        errors = [backend.read().error for backend in self.candidates]
        return MetricSnapshot(
            source="none",
            error="No metric backend detected. " + " ".join(e for e in errors if e),
        )


def build_metrics_backend(config: ExperimentConfig) -> AutoMetricsBackend:
    return AutoMetricsBackend(config)


def _find_jsonl_file(config: ExperimentConfig) -> Path | None:
    candidates: list[Path] = []
    for base in _candidate_dirs(config):
        candidates.extend([
            base / "metrics.jsonl",
            base / "metrics" / "metrics.jsonl",
            base / "logs" / "metrics.jsonl",
            base / "runs" / "metrics.jsonl",
        ])
        candidates.extend(sorted(base.glob("*.jsonl")))
        candidates.extend(sorted((base / "runs").glob("**/*.jsonl")) if (base / "runs").exists() else [])

    existing = [p for p in candidates if p.exists() and p.is_file()]
    if not existing:
        return None
    return max(existing, key=lambda p: p.stat().st_mtime)


def _find_tensorboard_event_file(config: ExperimentConfig) -> Path | None:
    files: list[Path] = []
    for base in _candidate_dirs(config):
        files.extend(sorted(base.glob("events.out.tfevents.*")))
        if (base / "runs").exists():
            files.extend(sorted((base / "runs").glob("**/events.out.tfevents.*")))
    existing = [p for p in files if p.exists() and p.is_file()]
    if not existing:
        return None
    return max(existing, key=lambda p: p.stat().st_mtime)


def _candidate_dirs(config: ExperimentConfig) -> list[Path]:
    raw_dirs = [config.metric_dir, config.working_dir]
    result: list[Path] = []
    for raw in raw_dirs:
        if not raw:
            continue
        path = Path(raw).expanduser()
        if not path.is_absolute() and config.working_dir:
            path = Path(config.working_dir).expanduser() / path
        if path.exists() and path.is_dir() and path not in result:
            result.append(path)
    return result


def _tail_lines(path: Path, max_lines: int) -> list[str]:
    # Metric files are usually small. Keep the simple path readable and bounded.
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-max_lines:]


def _point_from_json(raw: dict) -> MetricPoint:
    step = _optional_float(raw.get("step", raw.get("_step")))
    timestamp = _optional_float(raw.get("timestamp", raw.get("time", time.time())))
    metrics_raw = raw.get("metrics")
    if isinstance(metrics_raw, dict):
        values = {str(k): float(v) for k, v in metrics_raw.items() if _is_number(v)}
    else:
        skip = {"step", "_step", "timestamp", "time", "metrics"}
        values = {str(k): float(v) for k, v in raw.items() if k not in skip and _is_number(v)}
    return MetricPoint(values=values, step=step, timestamp=timestamp)


def _is_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    try:
        float(value)  # type: ignore[arg-type]
        return True
    except (TypeError, ValueError):
        return False


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
