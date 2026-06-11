#!/usr/bin/env python3
"""Fire the issue-#117 acceptance run: a CPU-only, non-LLM experiment
(hierarchical cascade multi-fidelity Bayesian optimization) through the
full 4->9 pipeline.

This is the exact failure shape from #117: BO code on gpytorch/botorch,
no GPU, no LLM — previously the 6b runner hung at producer_b because it
could not compose an execution for a non-LLM experiment. The acceptance
criteria for this run:
  1. Stage 6 COMPLETES (no producer_b hang) — the #117 repro.
  2. The receipt designates a VERIFIED environment BEFORE any experiment
     run (env_strategy + probe/env-build evidence — the environment-first
     contract).
  3. gpu_required: false → no GPU pick, CPU submission.
  4. Real RESULT_JSON metrics from the BO runs reach Stage 7/8.

Usage:
    python scripts/fire_mfbo_cpu.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = REPO_ROOT / ".onemancompany" / "company" / "business" / "projects"
SERVER_URL = "http://localhost:8000"
POLL_INTERVAL_SECONDS = 30
TIMEOUT_MINUTES = 150

TOPIC = (
    "Pilot study: does hierarchical cascade multi-fidelity Bayesian "
    "optimization (low-fidelity GP posterior used as the prior of the next "
    "fidelity, with a Value-of-Information acquisition that accounts for "
    "per-fidelity cost) beat standard baselines on synthetic benchmarks? "
    "SCOPE THIS AS A CPU-ONLY PILOT — this validates the pipeline on a "
    "non-LLM experiment (issue #117 acceptance): NO GPU, NO LLM inference. "
    "Stack: gpytorch + botorch + torch(CPU) + numpy/scipy. Benchmarks: "
    "Currin exponential (2-fidelity) and Branin (2-fidelity) with standard "
    "low-fidelity variants. Methods: (1) cascade GP + VoI (the proposed "
    "method), (2) MF-GP-UCB baseline, (3) single-fidelity GP-UCB. Seeds: "
    "exactly 10 (42..51), budget 30x low-fidelity cost per run. Metric: "
    "final simple regret vs ground-truth optimum; report mean +/- std per "
    "method per benchmark, Wilcoxon signed-rank cascade-vs-each-baseline, "
    "and wall-clock per run. H1: cascade+VoI achieves lower mean regret "
    "than single-fidelity GP-UCB on both benchmarks. This implements an "
    "UNPUBLISHED method — there is NO usable public upstream implementing "
    "the cascade prior-transfer + VoI combination, so expect the codebase "
    "pin to say NO USABLE UPSTREAM FOUND and Stage 6a to take the "
    "from-scratch path with a pinned requirements.txt. The execution "
    "environment MUST be verified ready (or built via the uv-venv "
    "env-build) BEFORE any experiment run, per the receipt's Environment "
    "designation contract; gpu_required: false. Smoke = 2 seeds x 1 "
    "benchmark x all 3 methods (<=5 min CPU). Full pilot = 10 seeds x 2 "
    "benchmarks x 3 methods, expected <=25 min total on CPU. Print one "
    "RESULT_JSON line aggregating per-method regret stats."
)


def _fire() -> str:
    resp = requests.post(
        f"{SERVER_URL}/api/ceo/task",
        data={
            "task": TOPIC,
            "start_stage": "4",
            "end_stage": "9",
            "auto_approve": "true",
            "mode": "standard",
            "paper_format": "markdown",
        },
        timeout=30,
    )
    resp.raise_for_status()
    pid = resp.json()["project_id"]
    print(f"[fire] new project_id = {pid}  (Stage 4 -> 9, MF-BO CPU pilot, auto_approve)", flush=True)
    return pid


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
    pid = _fire()
    iter_dir = PROJECTS_DIR / pid / "iterations" / "iter_001"
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
