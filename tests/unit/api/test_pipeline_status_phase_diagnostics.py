"""`/api/pipeline/{project_id}/status` must surface the diagnostics that
explain a non-running phase, not just the phase label.

Regression for the observability hole behind #8 / #30: once Stage 6b can
park in ``producer_b_waiting`` (a real experiment running for hours on
remote infra), an operator hitting /status sees ``phase:
producer_b_waiting`` but no answer to "waiting on what, since when?".
Likewise a ``failed`` pipeline returned a phase with no
``failure_reason``. The engine persists ``pending_run_ids``,
``pending_waiting_started_at`` and ``failure_reason`` — the status
endpoint must pass them through."""
from __future__ import annotations

import pytest

from onemancompany.core import pipeline_engine as pe


def _engine_on_disk(tmp_path, *, phase, extra):
    pe._active_pipelines.clear()
    pdir = tmp_path / "proj-x" / "iterations" / "iter_001"
    pdir.mkdir(parents=True)
    engine = pe.PipelineEngine("proj-x", str(pdir), "cot vs direct on Qwen")
    engine.state["current_stage"] = 6
    engine.state["phase"] = phase
    engine.state.update(extra)
    engine._save()
    pe._active_pipelines.clear()  # force /status to load fresh from disk
    return pdir


async def _call_status(monkeypatch, pdir):
    import onemancompany.core.project_archive as archive
    monkeypatch.setattr(archive, "get_project_dir", lambda pid: pdir)
    from onemancompany.api.routes import pipeline_status
    return await pipeline_status("proj-x")


@pytest.mark.asyncio
async def test_status_surfaces_waiting_diagnostics(tmp_path, monkeypatch):
    pdir = _engine_on_disk(
        tmp_path,
        phase="producer_b_waiting",
        extra={
            "pending_run_ids": ["run_a", "run_b"],
            "pending_waiting_started_at": "2026-06-03T10:00:00+00:00",
        },
    )
    resp = await _call_status(monkeypatch, pdir)

    assert resp["phase"] == "producer_b_waiting"
    assert resp["pending_run_ids"] == ["run_a", "run_b"], (
        "an operator must see which runs the pipeline is waiting on"
    )
    assert resp["pending_waiting_started_at"] == "2026-06-03T10:00:00+00:00", (
        "and since when, to judge against the max-wait deadline"
    )


@pytest.mark.asyncio
async def test_status_surfaces_failure_reason(tmp_path, monkeypatch):
    pdir = _engine_on_disk(
        tmp_path,
        phase="failed",
        extra={"failure_reason": "stage_6_waiting_timeout_43200s"},
    )
    resp = await _call_status(monkeypatch, pdir)

    assert resp["phase"] == "failed"
    assert resp["failure_reason"] == "stage_6_waiting_timeout_43200s", (
        "a failed pipeline must report WHY, not just that it failed"
    )


@pytest.mark.asyncio
async def test_status_running_pipeline_has_no_spurious_diagnostics(tmp_path, monkeypatch):
    """A healthy mid-run pipeline carries no failure_reason and no pending
    runs — the fields are present but falsy, never stale leftovers."""
    pdir = _engine_on_disk(tmp_path, phase="producer", extra={})
    resp = await _call_status(monkeypatch, pdir)

    assert resp["phase"] == "producer"
    assert not resp.get("failure_reason")
    assert not resp.get("pending_run_ids")
