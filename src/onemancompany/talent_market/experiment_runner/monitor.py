"""Supervisor loop for launched experiments."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from onemancompany.talent_market.experiment_runner.backends import MetricsBackend
from onemancompany.talent_market.experiment_runner.kill_rules import (
    best_metric,
    build_kill_rules,
    target_reached,
)
from onemancompany.talent_market.experiment_runner.launcher import (
    terminate_process_group,
    wait_for_readers,
)
from onemancompany.talent_market.experiment_runner.models import (
    ExperimentConfig,
    MetricSnapshot,
    MonitorResult,
    ProcessHandle,
)
from onemancompany.talent_market.experiment_runner.system_probe import SystemProbe


async def monitor_experiment(
    handle: ProcessHandle,
    config: ExperimentConfig,
    backend: MetricsBackend,
    on_log: Callable[[str, str], None] | None = None,
) -> MonitorResult:
    """Poll metrics and system state until the process exits or a kill rule fires."""

    started = time.monotonic()
    probe = SystemProbe(config)
    rules = build_kill_rules(config)
    latest_snapshot = MetricSnapshot(source="none")
    latest_system: dict = {}
    reached = False
    best = None

    while handle.is_alive():
        elapsed = time.monotonic() - started
        latest_snapshot = backend.read()
        latest_system = probe.read(handle.pid)
        reached = reached or target_reached(latest_snapshot, config)
        best = best_metric(latest_snapshot, config) if latest_snapshot.points else best

        if on_log:
            _emit_progress(on_log, latest_snapshot, latest_system, elapsed)

        for rule in rules:
            verdict = rule.evaluate(latest_snapshot, config, latest_system, elapsed)
            if verdict.should_kill:
                await terminate_process_group(handle, verdict.reason, on_log=on_log)
                await wait_for_readers(handle)
                return MonitorResult(
                    status="killed",
                    reason=verdict.reason,
                    backend=latest_snapshot.source,
                    latest_snapshot=latest_snapshot,
                    best_metric=best,
                    target_reached=reached,
                    system=latest_system,
                    returncode=handle.process.returncode,
                )

        try:
            await asyncio.wait_for(handle.process.wait(), timeout=config.poll_interval_seconds)
        except asyncio.TimeoutError:
            pass

    returncode = await handle.process.wait()
    await wait_for_readers(handle)
    latest_snapshot = backend.read()
    latest_system = probe.read(handle.pid)
    reached = reached or target_reached(latest_snapshot, config)
    best = best_metric(latest_snapshot, config) if latest_snapshot.points else best
    status = "completed" if returncode == 0 else "failed"
    reason = "Process exited normally." if returncode == 0 else f"Process exited with code {returncode}."

    return MonitorResult(
        status=status,
        reason=reason,
        backend=latest_snapshot.source,
        latest_snapshot=latest_snapshot,
        best_metric=best,
        target_reached=reached,
        system=latest_system,
        returncode=returncode,
    )


def _emit_progress(
    on_log: Callable[[str, str], None],
    snapshot: MetricSnapshot,
    system: dict,
    elapsed: float,
) -> None:
    latest = snapshot.latest
    metric_text = ""
    if latest and latest.values:
        pairs = ", ".join(f"{k}={v:g}" for k, v in sorted(latest.values.items())[:6])
        metric_text = f" metrics: {pairs}"
    elif snapshot.error:
        metric_text = f" metrics: {snapshot.error}"

    gpu_text = ""
    if system.get("gpu_utilization_max_percent") is not None:
        gpu_text = f" gpu={system['gpu_utilization_max_percent']:g}%"
    disk_text = f" disk_free={system['disk_free_gb']}GB" if system.get("disk_free_gb") is not None else ""
    on_log("progress", f"elapsed={elapsed:.0f}s backend={snapshot.source}{metric_text}{gpu_text}{disk_text}")
