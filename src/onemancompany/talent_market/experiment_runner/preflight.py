"""Preflight checks for experiment tasks."""

from __future__ import annotations

import shutil
from pathlib import Path

from onemancompany.talent_market.experiment_runner.models import ExperimentConfig


def run_preflight(config: ExperimentConfig) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) before launching the user's command."""

    errors: list[str] = []
    warnings: list[str] = []

    if not config.command.strip():
        errors.append("No command was provided.")

    if config.working_dir:
        work_dir = Path(config.working_dir).expanduser()
        if not work_dir.exists():
            errors.append(f"working_dir does not exist: {work_dir}")
        elif not work_dir.is_dir():
            errors.append(f"working_dir is not a directory: {work_dir}")
    else:
        warnings.append("No working_dir was provided; using the current process directory.")

    backend = config.monitor_backend.lower()
    if backend in {"tensorboard", "auto"}:
        if backend == "tensorboard" and not _has_tensorboard_package():
            warnings.append("TensorBoard backend requested, but optional package 'tensorboard' is not installed.")

    if backend == "wandb" and not _has_wandb_package():
        warnings.append("W&B backend requested, but optional package 'wandb' is not installed.")

    if config.device_backend == "nvidia" and not shutil.which("nvidia-smi"):
        warnings.append("NVIDIA device backend requested, but nvidia-smi was not found.")

    return errors, warnings


def _has_tensorboard_package() -> bool:
    try:
        import tensorboard  # noqa: F401
        return True
    except Exception:
        return False


def _has_wandb_package() -> bool:
    try:
        import wandb  # noqa: F401
        return True
    except Exception:
        return False
