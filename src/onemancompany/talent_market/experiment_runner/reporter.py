"""Final report rendering for experiment runner tasks."""

from __future__ import annotations

from onemancompany.talent_market.experiment_runner.models import ExperimentResult


def render_report(result: ExperimentResult) -> str:
    cfg = result.config
    mon = result.monitor
    latest = mon.latest_snapshot.latest if mon.latest_snapshot else None

    lines = [
        "# Experiment Report",
        "",
        f"- Status: {mon.status}",
        f"- Reason: {mon.reason or 'n/a'}",
        f"- Duration: {result.duration_seconds:.1f}s",
        f"- Command: `{cfg.command}`",
        f"- Working directory: `{cfg.working_dir or '.'}`",
        f"- Monitor backend: {mon.backend or cfg.monitor_backend}",
    ]

    if cfg.target_metric:
        lines.append(f"- Target metric: `{cfg.target_metric}`")
    if cfg.target_value is not None:
        lines.append(f"- Target value: {cfg.target_value:g}")
        lines.append(f"- Target reached: {'yes' if mon.target_reached else 'no'}")
    if mon.best_metric is not None:
        lines.append(f"- Best observed metric: {mon.best_metric:g}")
    if mon.returncode is not None:
        lines.append(f"- Process return code: {mon.returncode}")

    if result.preflight_warnings:
        lines.extend(["", "## Preflight Warnings"])
        lines.extend(f"- {warning}" for warning in result.preflight_warnings)

    if latest and latest.values:
        lines.extend(["", "## Latest Metrics"])
        for key, value in sorted(latest.values.items()):
            lines.append(f"- `{key}`: {value:g}")
    elif mon.latest_snapshot and mon.latest_snapshot.error:
        lines.extend(["", "## Metrics"])
        lines.append(mon.latest_snapshot.error)

    if mon.system:
        lines.extend(["", "## System Snapshot"])
        for key in ("os", "device_backend", "gpu_utilization_max_percent", "disk_free_gb"):
            if key in mon.system and mon.system[key] is not None:
                lines.append(f"- {key}: {mon.system[key]}")
        if mon.system.get("gpu_note"):
            lines.append(f"- gpu_note: {mon.system['gpu_note']}")
        if mon.system.get("gpu_error"):
            lines.append(f"- gpu_error: {mon.system['gpu_error']}")

    if result.stderr_tail:
        lines.extend(["", "## stderr Tail", "```text"])
        lines.extend(result.stderr_tail[-30:])
        lines.append("```")
    if result.stdout_tail:
        lines.extend(["", "## stdout Tail", "```text"])
        lines.extend(result.stdout_tail[-30:])
        lines.append("```")

    return "\n".join(lines).strip() + "\n"
