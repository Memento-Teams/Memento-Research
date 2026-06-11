#!/usr/bin/env python3
"""Fire the REAL-SCALE validation run: full GSM8K test split on Qwen2.5-7B,
CoT vs direct, vLLM batched inference.

This is the run that proves (or falsifies) this session's work in production:
- Parallelism-first runbook + DESCRIPTION (#13): 6a must produce vLLM batched
  code, not a per-example generate() loop (static gate would block it).
- 2638 generations sequentially would take hours — vLLM batched on one H100
  should take minutes. The wall-clock IS the assertion.
- If the full run exceeds the runner's 9-min budget, producer_b_waiting +
  run_tracker collection (#107/#30/#28) get exercised live.

Usage:
    python scripts/fire_gsm8k_real.py
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
    "Does chain-of-thought prompting beat direct-answer prompting on "
    "Qwen2.5-7B-Instruct on the REAL GSM8K benchmark? Run the FULL GSM8K test "
    "split — all 1319 problems — loaded via "
    "datasets.load_dataset('openai/gsm8k', 'main', split='test'). The dataset "
    "is pre-cached on the remote host (verified in setup.huggingface.datasets); "
    "do NOT embed a synthetic dataset and do NOT subsample the full run. "
    "Exactly ONE model: Qwen2.5-7B-Instruct at "
    "/mnt/data0/hf_models/Qwen/Qwen2.5-7B-Instruct. Greedy decoding "
    "(temperature=0), one H100. Two conditions: (A) direct — answer with only "
    "the final integer, max_tokens=16; (B) cot — think step by step then state "
    "'The answer is N.', max_tokens=512. "
    "MANDATORY: use vLLM BATCHED inference (the default runtime image ships "
    "vLLM): build the FULL prompt list for all 1319 x 2 cells and pass it to "
    "llm.chat(...) in as few calls as possible — a per-example "
    "model.generate() Python loop is forbidden and fails the static gate. "
    "Extract the gold answer from the '#### N' suffix of each GSM8K answer; "
    "extract predictions with regex (prefer 'answer is N', fall back to last "
    "integer). Metric: paired accuracy; H1 = accuracy_cot - accuracy_direct "
    ">= 0.10. Report accuracy per condition, the paired diff, a 95% CI "
    "(Wilson or bootstrap), and truncation counts per condition. Smoke = "
    "first 10 problems (<=5 min). Expected FULL wall-clock on one H100 with "
    "vLLM batching: 10-25 minutes including model load — report the actual "
    "generation wall-clock in RESULT_JSON as well."
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
    print(f"[fire] new project_id = {pid}  (Stage 4 -> 9, REAL GSM8K, auto_approve)", flush=True)
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
