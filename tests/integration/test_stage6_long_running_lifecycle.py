"""Integration: the Stage 6b long-running experiment lifecycle, end to end.

The unit suite in ``test_pipeline_engine.py`` exercises each transition of
the ``producer_b_waiting`` waiter in isolation (park, all-terminal,
finalize, timeout). What it does NOT cover — and what burned us in
practice — is the *composed* flow across a persisted ``pipeline_state.yaml``
and a server restart, plus the interaction with the two disk-scanning
watchdogs (#30).

The real failure mode this guards against: a real experiment parks in
``producer_b_waiting`` for hours; the server restarts (or the startup
watchdog runs); the watchdog sees the Stage 6b runner node sitting
COMPLETED on disk and replays ``on_task_complete`` — which, racing with
run_tracker flipping the phase to ``producer_b_finalize``, re-fires the
stale 6b result as a finalize completion and double-dispatches the critic.

These tests drive the REAL engine state machine (only the LLM-boundary
dispatchers are stubbed) against REAL on-disk state, including a
simulated restart (registry cleared → reload from ``pipeline_state.yaml``),
to prove the waiter survives a restart and the watchdogs defer to
run_tracker (#28, #30)."""
from __future__ import annotations

import os
import time
from pathlib import Path

import yaml

from onemancompany.core import pipeline_engine as pe


# --- still_running 6b report → engine parks; terminal final report → critic.
_STILL_RUNNING_REPORT = (
    "## Stage 6b — interim report\n\n"
    "Submitted the full experiment; it exceeds my task budget, exiting early "
    "so the engine waiter can collect it.\n\n"
    "- run_id: run_qwen_cot\n"
    "- status: still_running\n\n"
    "- run_id: run_qwen_direct\n"
    "- status: running\n"
)
_FINAL_REPORT = (
    "## Stage 6b — FINAL report\n\n"
    "- run_id: run_qwen_cot\n"
    "- status: succeeded\n"
    "- accuracy: 1.00\n\n"
    "- run_id: run_qwen_direct\n"
    "- status: succeeded\n"
    "- accuracy: 0.00\n"
)


def _iter_dir(root: Path, pid: str = "p_long") -> Path:
    d = root / pid / "iterations" / "iter_001"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_task_tree(iter_dir: Path, node_id: str, status: str) -> None:
    """Mirror the on-disk tree the watchdogs read: the 6b runner node sits
    COMPLETED while the experiment runs on remote infra."""
    tree = {
        "project_id": iter_dir.parents[1].name,
        "nodes": {
            node_id: {
                "id": node_id,
                "parent_id": "",
                "children_ids": [],
                "employee_id": "00025",
                "title": "Stage 6b runner",
                "description": "submit + poll experiment",
                "node_type": "task",
                "status": status,
                "result": _STILL_RUNNING_REPORT,
            }
        },
    }
    (iter_dir / "task_tree.yaml").write_text(
        yaml.safe_dump(tree, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )


def _patch_boundaries(monkeypatch):
    """Stub only the true external boundaries — the LLM-task dispatchers and
    event emitters. Everything else (routing, parking, parsing, the
    waiter transitions) runs for real. Returns the recorders."""
    rec = {"finalize": [], "critic": [], "initial_b": [], "gate": [], "stage_events": []}

    def fake_finalize(self):
        rec["finalize"].append(True)
        # Real dispatch assigns a fresh node; mirror that so a later
        # on_task_complete is attributed to the finalize task.
        self.state["active_node_id"] = "node-6b-final"
        self.state["phase"] = self.state.get("phase", "")

    monkeypatch.setattr(pe.PipelineEngine, "_dispatch_producer_b_finalize", fake_finalize)
    monkeypatch.setattr(
        pe.PipelineEngine, "_dispatch_critic",
        lambda self, r: rec["critic"].append(r),
    )
    monkeypatch.setattr(
        pe.PipelineEngine, "_dispatch_producer_b",
        lambda self, feedback="": rec["initial_b"].append(feedback),
    )
    monkeypatch.setattr(
        pe.PipelineEngine, "_emit_gate_event",
        lambda self, stage_id, confidence=None, exhausted=False:
            rec["gate"].append((stage_id, confidence, exhausted)),
    )
    monkeypatch.setattr(
        pe.PipelineEngine, "_emit_stage_event",
        lambda self, *a, **k: rec["stage_events"].append((a, k)),
    )
    return rec


def test_full_long_running_lifecycle_survives_restart_and_watchdogs(tmp_path, monkeypatch):
    """park → persist → simulate restart → watchdogs defer → reload →
    on_runs_all_terminal → finalize → final report → critic — all on one
    persisted pipeline, with the critic dispatched EXACTLY once."""
    pe._active_pipelines.clear()
    rec = _patch_boundaries(monkeypatch)

    root = tmp_path
    iter_dir = _iter_dir(root)
    pid = "p_long"

    # --- 1. 6b runner finishes with still-running runs → engine parks.
    engine = pe.PipelineEngine(pid, str(iter_dir), "cot vs direct on Qwen2.5-7B")
    engine.state["current_stage"] = 6
    engine.state["phase"] = "producer_b"
    engine.state["active_node_id"] = "node-6b"
    engine.on_task_complete("00025", "node-6b", _STILL_RUNNING_REPORT)

    assert engine.state["phase"] == "producer_b_waiting"
    assert engine.state["pending_run_ids"] == ["run_qwen_cot", "run_qwen_direct"]
    assert rec["critic"] == [], "critic must not fire while runs are still active"
    # Parking left active_node_id pointing at the (now-completed) 6b node —
    # the exact condition that tempts the watchdog to replay.
    assert engine.state["active_node_id"] == "node-6b"

    # The waiting state is on disk; the tree shows the 6b node COMPLETED.
    _write_task_tree(iter_dir, "node-6b", status="completed")
    # Make the state file stale enough to cross the stuck-detector threshold,
    # as a real multi-hour wait would.
    old = time.time() - (pe.PIPELINE_STUCK_THRESHOLD_SECONDS + 600)
    os.utime(iter_dir / "pipeline_state.yaml", (old, old))

    # --- 2. Simulate a server restart: drop the in-memory registry, then run
    # BOTH watchdogs. They must DEFER to run_tracker — not replay the
    # completed 6b node, not flag the parked pipeline as stuck (#30).
    pe._active_pipelines.clear()
    recovered = pe.recover_stalled_pipelines(root)
    stuck = pe.detect_stuck_pipelines(root)
    assert recovered == 0, "watchdog must not replay on_task_complete for a parked waiter"
    assert stuck == [], "parked waiter must not be surfaced as stuck (run_tracker owns it)"

    # --- 3. Reload the engine from disk (post-restart). The waiting
    # bookkeeping must have survived the round-trip.
    engine2 = pe.get_or_load_pipeline(pid, str(iter_dir))
    assert engine2 is not None
    assert engine2.state["phase"] == "producer_b_waiting"
    assert engine2.state["pending_run_ids"] == ["run_qwen_cot", "run_qwen_direct"]

    # --- 4. run_tracker observes both runs terminal → finalize re-dispatch.
    engine2.on_runs_all_terminal()
    assert engine2.state["phase"] == "producer_b_finalize"
    assert rec["finalize"] == [True]
    assert rec["critic"] == [], "critic waits for the FINAL report, not the interim one"

    # --- 5. The finalize runner writes the final report → critic, once.
    engine2.on_task_complete("00025", "node-6b-final", _FINAL_REPORT)
    assert rec["critic"] == [_FINAL_REPORT], "critic must be dispatched exactly once, on the final report"
    assert "pending_run_ids" not in engine2.state, "waiting bookkeeping must be cleared"
    assert "pending_waiting_started_at" not in engine2.state
    # The initial submit-and-run path must NEVER have been re-invoked (it would
    # orphan + double-charge the already-running experiments).
    assert rec["initial_b"] == []


def test_wait_timeout_path_opens_exhausted_gate_end_to_end(tmp_path, monkeypatch):
    """park → max-wait deadline trips → run_tracker calls on_runs_wait_timeout
    → engine opens an EXHAUSTED gate with a forensic failure_reason (which
    #106 turns into phase=failed under auto_approve). Driven through the
    real park + timeout methods on a persisted pipeline."""
    pe._active_pipelines.clear()
    rec = _patch_boundaries(monkeypatch)

    iter_dir = _iter_dir(tmp_path, pid="p_hung")
    engine = pe.PipelineEngine("p_hung", str(iter_dir), "cot vs direct on Qwen2.5-7B")
    engine.state["current_stage"] = 6
    engine.state["phase"] = "producer_b"
    engine.state["active_node_id"] = "node-6b"
    engine.on_task_complete("00025", "node-6b", _STILL_RUNNING_REPORT)
    assert engine.state["phase"] == "producer_b_waiting"

    engine.on_runs_wait_timeout(wait_seconds=12 * 3600)

    assert engine.state["phase"] == "gate"
    assert engine.state["failure_reason"] == "stage_6_waiting_timeout_43200s"
    assert rec["gate"] == [(6, None, True)], "must open an EXHAUSTED gate (#106 then fails it)"
    assert rec["critic"] == [], "a timed-out experiment never reaches the critic"
    assert rec["finalize"] == []
