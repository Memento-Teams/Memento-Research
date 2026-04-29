"""Platform-aware system probes for experiment monitoring."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

from onemancompany.talent_market.experiment_runner.models import ExperimentConfig


class SystemProbe:
    """Read system state without hard dependencies such as psutil."""

    def __init__(self, config: ExperimentConfig) -> None:
        self.config = config
        self.system = platform.system().lower()
        self.device_backend = self._resolve_device_backend()

    def read(self, pid: int | None = None) -> dict[str, Any]:
        work_dir = Path(self.config.working_dir or ".").expanduser()
        result: dict[str, Any] = {
            "os": self.system or "unknown",
            "device_backend": self.device_backend,
            "pid": pid,
            "process_alive": _pid_alive(pid) if pid else None,
        }

        try:
            usage = shutil.disk_usage(work_dir if work_dir.exists() else ".")
            result.update({
                "disk_total_bytes": usage.total,
                "disk_free_bytes": usage.free,
                "disk_free_gb": round(usage.free / (1024 ** 3), 2),
            })
        except OSError as exc:
            result["disk_error"] = str(exc)

        if self.device_backend == "nvidia":
            result.update(_read_nvidia_smi())
        elif self.system == "darwin":
            result["gpu_note"] = (
                "macOS GPU utilization is not polled by default. "
                "Metric, process, memory, and disk checks remain active."
            )
        return result

    def _resolve_device_backend(self) -> str:
        requested = (self.config.device_backend or "auto").lower()
        if requested != "auto":
            return requested
        if shutil.which("nvidia-smi"):
            return "nvidia"
        if self.system == "darwin":
            machine = platform.machine().lower()
            return "apple_silicon" if machine in {"arm64", "aarch64"} else "macos"
        return "cpu"


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_nvidia_smi() -> dict[str, Any]:
    query = "utilization.gpu,memory.used,memory.total,temperature.gpu"
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                f"--query-gpu={query}",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        return {"gpu_error": str(exc)}

    if proc.returncode != 0:
        return {"gpu_error": proc.stderr.strip()[:500]}

    gpu_rows = []
    for line in proc.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 4:
            continue
        try:
            gpu_rows.append({
                "utilization_gpu_percent": float(parts[0]),
                "memory_used_mb": float(parts[1]),
                "memory_total_mb": float(parts[2]),
                "temperature_c": float(parts[3]),
            })
        except ValueError:
            continue
    if not gpu_rows:
        return {"gpu_error": "nvidia-smi returned no parseable GPU rows."}
    return {
        "gpus": gpu_rows,
        "gpu_utilization_max_percent": max(row["utilization_gpu_percent"] for row in gpu_rows),
    }
