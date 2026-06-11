#!/usr/bin/env python3
"""Issue-#117 acceptance: fire Stage 6->9 with the Stage-5 fixtures that
iamlilAJ posted on the issue (used VERBATIM), race-injected into the new
iteration dir — exactly the 'test Stage 6 directly without running
Stages 1-5' usage the comments describe.

Fixture (from /tmp/mfbo_fixture/):
  stage5_experiment_designer.md  — comment fixture, verbatim (844 lines)
  stage5_assignments.md          — comment fixture, verbatim + scope addendum
  stage5_codebase_pin.md         — NO USABLE UPSTREAM FOUND (from-scratch)
  stage4_methodology_designer.md — compact Stage-4 companion
  stage5_gate_review.md / stage5_debate_transcript.md / figure

Usage: python scripts/fire_mfbo_stage6_inject.py
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = REPO_ROOT / ".onemancompany" / "company" / "business" / "projects"
FIXTURE_DIR = Path("/tmp/mfbo_fixture")
SERVER_URL = "http://localhost:8000"
POLL_INTERVAL_SECONDS = 30
TIMEOUT_MINUTES = 120

TOPIC = (
    "Pipeline-acceptance pilot (issue #117): hierarchical cascade "
    "multi-fidelity Bayesian optimization (cascade GP posterior->prior "
    "transfer + cost-aware VoI acquisition) vs MF-GP-UCB and "
    "single-fidelity GP-UCB on Currin + Branin (2-fidelity), CPU-ONLY, "
    "no GPU, no LLM. The Stage 4/5 artifacts are ALREADY in the project "
    "workspace (stage4_methodology_designer.md, "
    "stage5_experiment_designer.md, stage5_assignments.md incl. the "
    "iteration scope addendum, stage5_codebase_pin.md = NO USABLE "
    "UPSTREAM FOUND -> from-scratch path). Honour them verbatim. "
    "IMPORTANT FOR STAGE 6a: the implementation ALREADY EXISTS — a "
    "vetted experiment.py (982 lines, passes the static gate) and a "
    "pinned requirements.txt are already staged in "
    "/tmp/stage6_impl/<project_id>/ (Step 0.0 inventory will find them). "
    "Do NOT rewrite or re-stage the code. Your remaining work is ONLY "
    "the tail: Step 4.5 environment designation (probe; the infra conda "
    "env is expected to have gpytorch/botorch/scipy/pydantic), Phase 4 "
    "push, Step 4.6 run-the-smoke-yourself (gpu_required: false), and "
    "the Phase 5 receipt with smoke_validated_by_6a evidence. "
    "Smoke entrypoint: python experiment.py --smoke --seed 42. "
    "Pilot (6b runs this): seeds 42-51 x {currin,branin} x 3 methods, "
    "<=25 min CPU. Environment must be verified ready BEFORE any "
    "experiment run; gpu_required: false."
)
STAGING_SRC = Path("/tmp/mfbo_staging")  # experiment.py + requirements.txt


def _fire() -> str:
    resp = requests.post(
        f"{SERVER_URL}/api/ceo/task",
        data={
            "task": TOPIC,
            "start_stage": "6",
            "end_stage": "9",
            "auto_approve": "true",
            "mode": "standard",
            "paper_format": "markdown",
        },
        timeout=30,
    )
    resp.raise_for_status()
    pid = resp.json()["project_id"]
    print(f"[fire] project_id = {pid}  (Stage 6 -> 9, MF-BO fixture inject)", flush=True)
    return pid


def _inject(iter_dir: Path, pid: str) -> None:
    iter_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in FIXTURE_DIR.iterdir():
        if f.is_file():
            shutil.copy2(f, iter_dir / f.name)
            n += 1
    print(f"[inject] {n} fixture files -> {iter_dir}", flush=True)
    # Seed the 6a staging area with the issue-posted, gate-clean
    # implementation (Anjie's intended 'pair with the posted
    # experiment.py' usage) so 6a only finishes the tail.
    staging = Path("/tmp/stage6_impl") / pid
    staging.mkdir(parents=True, exist_ok=True)
    m = 0
    for f in STAGING_SRC.iterdir():
        if f.is_file():
            shutil.copy2(f, staging / f.name)
            m += 1
    print(f"[inject] {m} staged impl files -> {staging}", flush=True)


def _snapshot(iter_dir: Path) -> dict:
    sp = iter_dir / "pipeline_state.yaml"
    if not sp.exists():
        return {"phase": "?", "stage": "?", "retries": 0}
    try:
        st = yaml.safe_load(sp.read_text()) or {}
    except yaml.YAMLError:
        return {"phase": "?yaml?", "stage": "?", "retries": 0}
    return {
        "phase": st.get("phase", "?"),
        "stage": st.get("current_stage", "?"),
        "retries": st.get("retries", 0),
        "failure_reason": st.get("failure_reason", ""),
        "pending": st.get("pending_run_ids", []),
        "result_loops": st.get("result_loops"),
        "stage_results": sorted((st.get("stage_results") or {}).keys(),
                                key=lambda x: int(x) if str(x).isdigit() else 99),
    }


def main() -> int:
    try:
        requests.get(f"{SERVER_URL}/api/employees", timeout=5).raise_for_status()
    except requests.RequestException as exc:
        print(f"FATAL: server not reachable: {exc}", file=sys.stderr)
        return 2
    if not FIXTURE_DIR.exists():
        print(f"FATAL: fixture dir missing: {FIXTURE_DIR}", file=sys.stderr)
        return 2
    pid = _fire()
    iter_dir = PROJECTS_DIR / pid / "iterations" / "iter_001"
    _inject(iter_dir, pid)
    deadline = time.time() + TIMEOUT_MINUTES * 60
    last = None
    while time.time() < deadline:
        snap = _snapshot(iter_dir)
        ts = time.strftime("%H:%M:%S")
        line = (f"  [{ts}] stage={snap['stage']} phase={snap['phase']} "
                f"retries={snap['retries']} done={snap['stage_results']}"
                + (f" pending={len(snap['pending'])}" if snap.get("pending") else "")
                + (f" loops={snap['result_loops']}" if snap.get("result_loops") else ""))
        if line != last:
            print(line, flush=True)
            last = line
        if snap["phase"] in ("done", "failed"):
            break
        time.sleep(POLL_INTERVAL_SECONDS)
    final = _snapshot(iter_dir)
    print()
    print("=" * 60)
    print(f"FINAL: stage={final['stage']} phase={final['phase']} retries={final['retries']}")
    if final.get("failure_reason"):
        print(f"failure_reason = {final['failure_reason']}")
    print(f"stages with results: {final['stage_results']}")
    print(f"project_id = {pid}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
