"""Deterministic kill rules for experiment supervision."""

from __future__ import annotations

import math
import time
from typing import Any, Protocol

from onemancompany.talent_market.experiment_runner.models import (
    ExperimentConfig,
    MetricSnapshot,
    RuleVerdict,
)


class KillRule(Protocol):
    name: str

    def evaluate(
        self,
        snapshot: MetricSnapshot,
        config: ExperimentConfig,
        system: dict[str, Any],
        elapsed_seconds: float,
    ) -> RuleVerdict: ...


class WallClockBudgetRule:
    name = "wall_clock_budget"

    def evaluate(self, snapshot, config, system, elapsed_seconds):
        if config.budget_seconds is not None and elapsed_seconds > config.budget_seconds:
            return RuleVerdict(True, f"Wall-clock budget exceeded ({elapsed_seconds:.0f}s).", self.name)
        return RuleVerdict()


class NanLossRule:
    name = "nan_loss"

    def evaluate(self, snapshot, config, system, elapsed_seconds):
        latest = snapshot.latest
        if latest is None:
            return RuleVerdict()
        for key, value in latest.values.items():
            if "loss" in key.lower() and not math.isfinite(value):
                return RuleVerdict(True, f"{key} is not finite ({value}).", self.name)
        return RuleVerdict()


class PlateauRule:
    name = "metric_plateau"

    def evaluate(self, snapshot, config, system, elapsed_seconds):
        if elapsed_seconds < config.warmup_seconds:
            return RuleVerdict()
        patience = int((config.kill_rules or {}).get("plateau_patience") or 0)
        metric = config.target_metric
        if not patience or not metric:
            return RuleVerdict()
        series = snapshot.series(metric)
        if len(series) <= patience:
            return RuleVerdict()

        best_before = _best(series[:-patience], config.higher_is_better)
        best_recent = _best(series[-patience:], config.higher_is_better)
        improved = best_recent > best_before if config.higher_is_better else best_recent < best_before
        if not improved:
            direction = "higher" if config.higher_is_better else "lower"
            return RuleVerdict(
                True,
                f"{metric} plateaued for {patience} points; best recent={best_recent:g}, "
                f"previous best={best_before:g} ({direction} is better).",
                self.name,
            )
        return RuleVerdict()


class LossDivergenceRule:
    name = "loss_divergence"

    def evaluate(self, snapshot, config, system, elapsed_seconds):
        factor = float((config.kill_rules or {}).get("loss_divergence_factor") or 2.0)
        window = int((config.kill_rules or {}).get("loss_divergence_window") or 5)
        latest = snapshot.latest
        if latest is None:
            return RuleVerdict()
        for key in latest.values:
            if "loss" not in key.lower():
                continue
            series = snapshot.series(key)
            if len(series) < window + 1:
                continue
            baseline = sum(series[-window - 1:-1]) / window
            current = series[-1]
            if math.isfinite(baseline) and baseline > 0 and current > baseline * factor:
                return RuleVerdict(
                    True,
                    f"{key} diverged: current={current:g}, recent average={baseline:g}, factor={factor:g}.",
                    self.name,
                )
        return RuleVerdict()


class GpuIdleRule:
    name = "gpu_idle"

    def __init__(self) -> None:
        self.idle_since: float | None = None

    def evaluate(self, snapshot, config, system, elapsed_seconds):
        threshold_minutes = float((config.kill_rules or {}).get("gpu_idle_minutes") or 0)
        if not threshold_minutes:
            return RuleVerdict()
        util = system.get("gpu_utilization_max_percent")
        if util is None:
            return RuleVerdict()
        now = time.monotonic()
        if float(util) <= 0.0 and elapsed_seconds >= config.warmup_seconds:
            if self.idle_since is None:
                self.idle_since = now
            idle_seconds = now - self.idle_since
            if idle_seconds >= threshold_minutes * 60:
                return RuleVerdict(True, f"GPU idle for {idle_seconds:.0f}s.", self.name)
        else:
            self.idle_since = None
        return RuleVerdict()


class DiskFreeRule:
    name = "disk_near_full"

    def evaluate(self, snapshot, config, system, elapsed_seconds):
        min_free_gb = float((config.kill_rules or {}).get("disk_min_free_gb") or 0)
        if not min_free_gb:
            return RuleVerdict()
        free_gb = system.get("disk_free_gb")
        if free_gb is not None and float(free_gb) < min_free_gb:
            return RuleVerdict(True, f"Disk free space below {min_free_gb:g} GB ({free_gb:g} GB).", self.name)
        return RuleVerdict()


def build_kill_rules(config: ExperimentConfig) -> list[KillRule]:
    rules: list[KillRule] = [WallClockBudgetRule(), DiskFreeRule()]
    if (config.kill_rules or {}).get("nan_loss", True):
        rules.append(NanLossRule())
    rules.extend([LossDivergenceRule(), PlateauRule(), GpuIdleRule()])
    return rules


def target_reached(snapshot: MetricSnapshot, config: ExperimentConfig) -> bool:
    if config.target_value is None or not config.target_metric:
        return False
    latest = snapshot.latest
    if latest is None:
        return False
    value = latest.values.get(config.target_metric)
    if value is None:
        return False
    return value >= config.target_value if config.higher_is_better else value <= config.target_value


def best_metric(snapshot: MetricSnapshot, config: ExperimentConfig) -> float | None:
    if not config.target_metric:
        return None
    series = snapshot.series(config.target_metric)
    if not series:
        return None
    return _best(series, config.higher_is_better)


def _best(series: list[float], higher_is_better: bool) -> float:
    return max(series) if higher_is_better else min(series)
