"""#156: the engine prefers a deterministic Stage-6 submit (parameterised from
the 6a receipt) and degrades gracefully to the agent runner when infra is
unavailable. Tests the decision logic + the producer_b_waiting handoff."""
from __future__ import annotations

from types import SimpleNamespace

from onemancompany.agents import stage6_infra as s6
from onemancompany.core.pipeline_engine import PipelineEngine

RECEIPT = "### Smoke\npython exp.py --smoke --seed 42\n- **Local file**: `/tmp/x/exp.py`\n"


def _fake(tmp_path, scripts=None, config="/c"):
    return SimpleNamespace(
        project_dir=str(tmp_path), project_id="p1",
        _iteration_id=lambda: "iter_001",
        _stage6_infra_paths=lambda: (scripts if scripts is not None else {"fast_submit.sh": "a"}, config),
    )


def test_deterministic_submit_success_returns_run_ids(tmp_path, monkeypatch):
    (tmp_path / "stage6_implementation_receipt.md").write_text(RECEIPT, encoding="utf-8")
    monkeypatch.setattr(s6, "submit",
                        lambda *a, **k: s6.SubmitResult(ok=True, run_id="run_abc12345", kind="smoke"))
    out = PipelineEngine._try_deterministic_stage6_submit(_fake(tmp_path))
    assert out == {"run_ids": ["run_abc12345"]}


def test_no_receipt_falls_back_to_agent(tmp_path):
    fake = SimpleNamespace(project_dir=str(tmp_path), project_id="p1", _iteration_id=lambda: "iter_001")
    assert PipelineEngine._try_deterministic_stage6_submit(fake) is None


def test_no_infra_scripts_falls_back(tmp_path):
    (tmp_path / "stage6_implementation_receipt.md").write_text(RECEIPT, encoding="utf-8")
    # scripts={} (infra not found) → degrade
    assert PipelineEngine._try_deterministic_stage6_submit(_fake(tmp_path, scripts={}, config="")) is None


def test_submit_failure_falls_back(tmp_path, monkeypatch):
    (tmp_path / "stage6_implementation_receipt.md").write_text(RECEIPT, encoding="utf-8")
    monkeypatch.setattr(s6, "submit",
                        lambda *a, **k: s6.SubmitResult(ok=False, error="missing INFRA_SERVER_URL"))
    assert PipelineEngine._try_deterministic_stage6_submit(_fake(tmp_path)) is None


def test_dispatch_producer_b_parks_in_waiting_on_deterministic_submit(monkeypatch):
    state = {}
    fake = SimpleNamespace(
        state=state, current_stage=6,
        _stage_def=lambda *a: {"id": 6, "name": "Auto Experiment"},
        _try_deterministic_stage6_submit=lambda: {"run_ids": ["run_xy987654"]},
        _save=lambda: None,
        _emit_stage_event=lambda *a, **k: None,
    )
    PipelineEngine._dispatch_producer_b(fake)
    assert state["phase"] == "producer_b_waiting"
    assert state["pending_run_ids"] == ["run_xy987654"]
    assert "run_xy987654" in state["stage_6_runs"]
