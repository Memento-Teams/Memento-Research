"""BaseAgentRunner adapter for the built-in experiment runner talent."""

from __future__ import annotations

import time
from collections.abc import Callable

from onemancompany.agents.base import BaseAgentRunner
from onemancompany.core.config import STATUS_IDLE, STATUS_WORKING
from onemancompany.talent_market.experiment_runner.backends import build_metrics_backend
from onemancompany.talent_market.experiment_runner.launcher import launch_experiment
from onemancompany.talent_market.experiment_runner.models import (
    ExperimentResult,
    MonitorResult,
)
from onemancompany.talent_market.experiment_runner.monitor import monitor_experiment
from onemancompany.talent_market.experiment_runner.parser import (
    ExperimentConfigError,
    parse_task_config,
)
from onemancompany.talent_market.experiment_runner.preflight import run_preflight
from onemancompany.talent_market.experiment_runner.reporter import render_report


class ExperimentRunnerAgent(BaseAgentRunner):
    """Deterministic experiment executor with local-first metrics monitoring."""

    role = "Experiment Runner"

    def __init__(self, employee_id: str) -> None:
        self.employee_id = employee_id
        self._last_usage = {
            "model": "deterministic/experiment-runner",
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
        }

    async def run(self, task: str) -> str:
        return await self.run_streamed(task)

    async def run_streamed(
        self,
        task: str,
        on_log: Callable[[str, str], None] | None = None,
    ) -> str:
        self._set_status(STATUS_WORKING)
        started = time.monotonic()
        try:
            try:
                config = parse_task_config(task)
            except ExperimentConfigError as exc:
                return f"Experiment task rejected: {exc}\n"

            errors, warnings = run_preflight(config)
            for warning in warnings:
                _log(on_log, "warning", warning)
            if errors:
                return "Experiment task rejected:\n" + "\n".join(f"- {error}" for error in errors) + "\n"

            backend = build_metrics_backend(config)
            backend.detect()
            _log(on_log, "config", f"backend={backend.name} command={config.command}")

            handle = await launch_experiment(config, on_log=on_log)
            monitor_result = await monitor_experiment(handle, config, backend, on_log=on_log)
            result = ExperimentResult(
                config=config,
                monitor=monitor_result,
                duration_seconds=time.monotonic() - started,
                stdout_tail=handle.stdout_tail,
                stderr_tail=handle.stderr_tail,
                preflight_warnings=warnings,
            )
            return render_report(result)
        except Exception as exc:
            monitor = MonitorResult(status="failed", reason=f"Runner error: {exc}")
            try:
                config = parse_task_config(task)
                result = ExperimentResult(
                    config=config,
                    monitor=monitor,
                    duration_seconds=time.monotonic() - started,
                )
                return render_report(result)
            except Exception:
                return f"Experiment runner failed before launch: {exc}\n"
        finally:
            self._set_status(STATUS_IDLE)


def _log(on_log: Callable[[str, str], None] | None, log_type: str, content: str) -> None:
    if on_log:
        on_log(log_type, content)
