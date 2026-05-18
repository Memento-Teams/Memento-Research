"""Task parsing for local-first experiment execution."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from onemancompany.talent_market.experiment_runner.models import ExperimentConfig


_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
_PROJECT_WORKSPACE_RE = re.compile(r"\[Project workspace:\s*(.+?)\s+(?:-|--|—)\s+save all outputs here\]", re.IGNORECASE)


class ExperimentConfigError(ValueError):
    """Raised when a task cannot be turned into an experiment config."""


def parse_task_config(task_text: str) -> ExperimentConfig:
    """Extract an ExperimentConfig from JSON or a short natural-language request."""

    raw = _extract_json_object(task_text)
    if raw:
        return _config_from_mapping(raw, task_text)
    return _config_from_text(task_text)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    for match in _FENCED_JSON_RE.finditer(text):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("command"):
            return data

    decoder = json.JSONDecoder()
    for idx, char in enumerate(text):
        if char != "{":
            continue
        try:
            data, _ = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("command"):
            return data
    return None


def _config_from_mapping(data: dict[str, Any], source_text: str) -> ExperimentConfig:
    command = str(data.get("command") or "").strip()
    if not command:
        raise ExperimentConfigError("Experiment task must include a command.")

    working_dir = str(data.get("working_dir") or data.get("work_dir") or "").strip()
    if not working_dir:
        working_dir = _extract_project_workspace(source_text)

    budget_seconds = _coerce_budget_seconds(data)
    kill_rules = dict(data.get("kill_rules") or {})
    poll_interval = float(data.get("poll_interval_seconds") or data.get("poll_interval") or 60.0)
    warmup_seconds = float(data.get("warmup_seconds") or data.get("warmup") or 120.0)

    return ExperimentConfig(
        command=command,
        working_dir=working_dir,
        extra_env={str(k): str(v) for k, v in dict(data.get("extra_env") or {}).items()},
        monitor_backend=str(data.get("monitor_backend") or data.get("backend") or "auto").lower(),
        metric_dir=str(data.get("metric_dir") or data.get("log_dir") or "").strip(),
        target_metric=str(data.get("target_metric") or data.get("metric") or "").strip(),
        target_value=_optional_float(data.get("target_value")),
        higher_is_better=bool(data.get("higher_is_better", True)),
        budget_seconds=budget_seconds,
        poll_interval_seconds=max(0.1, poll_interval),
        warmup_seconds=max(0.0, warmup_seconds),
        kill_rules=kill_rules,
        wandb_project=str(data.get("wandb_project") or "").strip(),
        wandb_run_id=str(data.get("wandb_run_id") or data.get("wandb_run") or "").strip(),
        device_backend=str(data.get("device_backend") or "auto").lower(),
    )


def _config_from_text(text: str) -> ExperimentConfig:
    command = _extract_command(text)
    if not command:
        raise ExperimentConfigError(
            "Experiment task must include a command. Use JSON with a 'command' field "
            "or write 'Run <command> from <working_dir>'."
        )

    working_dir = _extract_field(text, "working_dir") or _extract_field(text, "work_dir")
    if not working_dir:
        working_dir = _extract_from_phrase(text)
    if not working_dir:
        working_dir = _extract_project_workspace(text)

    monitor_backend = "auto"
    lowered = text.lower()
    if "tensorboard" in lowered:
        monitor_backend = "tensorboard"
    elif "wandb" in lowered or "w&b" in lowered:
        monitor_backend = "wandb"
    elif "metrics.jsonl" in lowered or "jsonl" in lowered:
        monitor_backend = "jsonl"

    metric_dir = _extract_field(text, "metric_dir") or _extract_field(text, "log_dir")
    target_metric = _extract_field(text, "target_metric") or _extract_watch_metric(text)
    target_value = _extract_target_value(text)
    budget_seconds = _extract_budget_seconds(text)

    return ExperimentConfig(
        command=command,
        working_dir=working_dir,
        monitor_backend=monitor_backend,
        metric_dir=metric_dir,
        target_metric=target_metric,
        target_value=target_value,
        budget_seconds=budget_seconds,
        kill_rules=_extract_text_kill_rules(text),
    )


def _extract_command(text: str) -> str:
    explicit = _extract_field(text, "command")
    if explicit:
        return explicit

    code_run = re.search(r"\bRun\s+`([^`]+)`", text, re.IGNORECASE)
    if code_run:
        return code_run.group(1).strip()

    quoted_run = re.search(r"\bRun\s+\"([^\"]+)\"", text, re.IGNORECASE)
    if quoted_run:
        return quoted_run.group(1).strip()

    phrase = re.search(r"\bRun\s+(.+?)\s+from\s+(`[^`]+`|\"[^\"]+\"|\S+)", text, re.IGNORECASE | re.DOTALL)
    if phrase:
        return _clean_inline(phrase.group(1))
    return ""


def _extract_field(text: str, name: str) -> str:
    match = re.search(rf"(?im)^\s*{re.escape(name)}\s*[:=]\s*(.+?)\s*$", text)
    if not match:
        return ""
    return _clean_inline(match.group(1))


def _extract_from_phrase(text: str) -> str:
    match = re.search(r"\bfrom\s+(`[^`]+`|\"[^\"]+\"|/[^\s.,]+|~[^\s.,]+)", text, re.IGNORECASE)
    return _clean_inline(match.group(1)) if match else ""


def _extract_project_workspace(text: str) -> str:
    match = _PROJECT_WORKSPACE_RE.search(text)
    return match.group(1).strip() if match else ""


def _extract_watch_metric(text: str) -> str:
    match = re.search(r"\b(?:watch|track|monitor)\s+`?([A-Za-z0-9_./:-]+)`?", text, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _extract_target_value(text: str) -> float | None:
    percent = re.search(r"(?:hit|reach|target|>=|above)\s+([0-9]+(?:\.[0-9]+)?)\s*%", text, re.IGNORECASE)
    if percent:
        return float(percent.group(1)) / 100.0
    decimal = re.search(r"(?:target_value|target|>=)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE)
    return float(decimal.group(1)) if decimal else None


def _extract_budget_seconds(text: str) -> float | None:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(hours?|hrs?|h|minutes?|mins?|m)\b", text, re.IGNORECASE)
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2).lower()
    return amount * 3600 if unit.startswith(("h", "hour", "hr")) else amount * 60


def _extract_text_kill_rules(text: str) -> dict[str, Any]:
    rules: dict[str, Any] = {}
    lowered = text.lower()
    if "nan" in lowered:
        rules["nan_loss"] = True
    plateau = re.search(r"plateau(?:_patience)?\s*[:=]?\s*(\d+)", text, re.IGNORECASE)
    if plateau:
        rules["plateau_patience"] = int(plateau.group(1))
    idle = re.search(r"gpu[_ -]?idle(?:_minutes)?\s*[:=]?\s*(\d+)", text, re.IGNORECASE)
    if idle:
        rules["gpu_idle_minutes"] = int(idle.group(1))
    return rules


def _coerce_budget_seconds(data: dict[str, Any]) -> float | None:
    if data.get("budget_seconds") is not None:
        return float(data["budget_seconds"])
    if data.get("budget_minutes") is not None:
        return float(data["budget_minutes"]) * 60
    if data.get("budget_hours") is not None:
        return float(data["budget_hours"]) * 3600
    return None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _clean_inline(value: str) -> str:
    value = value.strip().strip(",.;")
    if (value.startswith("`") and value.endswith("`")) or (
        value.startswith('"') and value.endswith('"')
    ):
        value = value[1:-1]
    return str(Path(value).expanduser()) if value.startswith("~") else value
