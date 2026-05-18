"""Shared data models for the experiment runner runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExperimentConfig:
    """Structured configuration extracted from an experiment task."""

    command: str
    working_dir: str = ""
    extra_env: dict[str, str] = field(default_factory=dict)
    monitor_backend: str = "auto"
    metric_dir: str = ""
    target_metric: str = ""
    target_value: float | None = None
    higher_is_better: bool = True
    budget_seconds: float | None = None
    poll_interval_seconds: float = 60.0
    warmup_seconds: float = 120.0
    kill_rules: dict[str, Any] = field(default_factory=dict)
    wandb_project: str = ""
    wandb_run_id: str = ""
    device_backend: str = "auto"

    def resolved_metric_dir(self) -> str:
        return self.metric_dir or self.working_dir


@dataclass
class MetricPoint:
    """One metric observation from JSONL, TensorBoard, W&B, or another backend."""

    values: dict[str, float]
    step: float | None = None
    timestamp: float | None = None


@dataclass
class MetricSnapshot:
    """Latest metric state returned by a monitoring backend."""

    source: str
    points: list[MetricPoint] = field(default_factory=list)
    error: str = ""
    stale: bool = False

    @property
    def latest(self) -> MetricPoint | None:
        return self.points[-1] if self.points else None

    def series(self, key: str) -> list[float]:
        result: list[float] = []
        for point in self.points:
            value = point.values.get(key)
            if value is not None:
                result.append(value)
        return result


@dataclass
class RuleVerdict:
    """Kill-rule decision."""

    should_kill: bool = False
    reason: str = ""
    rule: str = ""


@dataclass
class MonitorResult:
    """Result of supervising a launched experiment process."""

    status: str
    reason: str = ""
    backend: str = ""
    latest_snapshot: MetricSnapshot | None = None
    best_metric: float | None = None
    target_reached: bool = False
    system: dict[str, Any] = field(default_factory=dict)
    returncode: int | None = None


@dataclass
class ProcessHandle:
    """Subprocess state used by the launcher and monitor."""

    pid: int
    process: Any
    command: str
    stdout_tail: list[str] = field(default_factory=list)
    stderr_tail: list[str] = field(default_factory=list)
    reader_tasks: list[Any] = field(default_factory=list)

    def is_alive(self) -> bool:
        return self.process.returncode is None


@dataclass
class ExperimentResult:
    """End-to-end task outcome used by the reporter."""

    config: ExperimentConfig
    monitor: MonitorResult
    duration_seconds: float
    stdout_tail: list[str] = field(default_factory=list)
    stderr_tail: list[str] = field(default_factory=list)
    preflight_errors: list[str] = field(default_factory=list)
    preflight_warnings: list[str] = field(default_factory=list)
