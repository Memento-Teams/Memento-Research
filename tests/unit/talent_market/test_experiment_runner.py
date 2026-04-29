from __future__ import annotations

import json
import shlex
import sys

import pytest

from onemancompany.talent_market.experiment_runner.backends import JsonlMetricsBackend
from onemancompany.talent_market.experiment_runner.kill_rules import PlateauRule, target_reached
from onemancompany.talent_market.experiment_runner.models import ExperimentConfig, MetricPoint, MetricSnapshot
from onemancompany.talent_market.experiment_runner.parser import parse_task_config
from onemancompany.talent_market.experiment_runner.runner import ExperimentRunnerAgent


def test_parse_json_config():
    cfg = parse_task_config(
        """
        ```json
        {
          "command": "python train.py",
          "working_dir": "/tmp/project",
          "monitor_backend": "jsonl",
          "target_metric": "val_acc",
          "target_value": 0.9,
          "budget_minutes": 30,
          "kill_rules": {"plateau_patience": 3}
        }
        ```
        """
    )

    assert cfg.command == "python train.py"
    assert cfg.working_dir == "/tmp/project"
    assert cfg.monitor_backend == "jsonl"
    assert cfg.target_metric == "val_acc"
    assert cfg.target_value == 0.9
    assert cfg.budget_seconds == 1800
    assert cfg.kill_rules["plateau_patience"] == 3


def test_parse_natural_language_preserves_command():
    cfg = parse_task_config(
        "Run `bash scripts/train.sh --epochs 10 --bs 32` from `/repo`. "
        "Watch `val_acc`. Kill it if it does not hit 90% in 4 hours."
    )

    assert cfg.command == "bash scripts/train.sh --epochs 10 --bs 32"
    assert cfg.working_dir == "/repo"
    assert cfg.target_metric == "val_acc"
    assert cfg.target_value == 0.9
    assert cfg.budget_seconds == 4 * 3600


def test_jsonl_backend_reads_flat_and_nested_metrics(tmp_path):
    metrics = tmp_path / "metrics.jsonl"
    metrics.write_text(
        "\n".join([
            json.dumps({"step": 1, "val_acc": 0.7, "loss": 1.2}),
            json.dumps({"step": 2, "metrics": {"val_acc": 0.8, "loss": 0.9}}),
        ])
    )

    backend = JsonlMetricsBackend(ExperimentConfig(command="true", working_dir=str(tmp_path)))

    assert backend.detect()
    snapshot = backend.read()
    assert snapshot.source == "jsonl"
    assert snapshot.series("val_acc") == [0.7, 0.8]
    assert snapshot.latest is not None
    assert snapshot.latest.values["loss"] == 0.9


def test_plateau_rule_kills_when_metric_stops_improving():
    snapshot = MetricSnapshot(
        source="jsonl",
        points=[
            MetricPoint({"val_acc": 0.5}),
            MetricPoint({"val_acc": 0.6}),
            MetricPoint({"val_acc": 0.6}),
            MetricPoint({"val_acc": 0.59}),
        ],
    )
    cfg = ExperimentConfig(
        command="true",
        target_metric="val_acc",
        kill_rules={"plateau_patience": 2},
    )

    verdict = PlateauRule().evaluate(snapshot, cfg, {}, elapsed_seconds=999)

    assert verdict.should_kill
    assert "plateaued" in verdict.reason


def test_target_reached_uses_direction():
    snapshot = MetricSnapshot(source="jsonl", points=[MetricPoint({"loss": 0.2})])
    cfg = ExperimentConfig(command="true", target_metric="loss", target_value=0.3, higher_is_better=False)
    assert target_reached(snapshot, cfg)


@pytest.mark.asyncio
async def test_experiment_runner_executes_local_jsonl_task(tmp_path):
    script = tmp_path / "train.py"
    script.write_text(
        "import json\n"
        "from pathlib import Path\n"
        "Path('metrics.jsonl').write_text(json.dumps({'step': 1, 'val_acc': 0.91, 'loss': 0.4}) + '\\n')\n"
        "print('done')\n"
    )
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
    task = json.dumps({
        "command": command,
        "working_dir": str(tmp_path),
        "monitor_backend": "jsonl",
        "target_metric": "val_acc",
        "target_value": 0.9,
        "poll_interval_seconds": 0.1,
        "warmup_seconds": 0,
    })

    logs: list[tuple[str, str]] = []
    agent = ExperimentRunnerAgent("00099")
    report = await agent.run_streamed(task, on_log=lambda t, c: logs.append((t, str(c))))

    assert "Status: completed" in report
    assert "Target reached: yes" in report
    assert "`val_acc`: 0.91" in report
    assert any(kind == "stdout" and "done" in content for kind, content in logs)
