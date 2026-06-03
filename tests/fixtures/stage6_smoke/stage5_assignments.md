# Stage 5 — Coordination Assignments

| # | Task | Assignee | Skill | Due | Acceptance criterion |
|---|------|----------|-------|-----|----------------------|
| T0 | Clone upstream `pypa/sampleproject@621e4974ca`, add `src/sample/benchmark.py` that loads `Qwen2.5-7B-Instruct` from `/mnt/data0/hf_models/Qwen/Qwen2.5-7B-Instruct` on `cuda:0` (BF16), runs the 10-problem GSM-style DATASET under two prompting strategies (direct vs CoT) using `tokenizer.apply_chat_template`, scores accuracy via the `extract_int` regex, prints the canonical `=== RESULT_JSON ===` envelope, edit `pyproject.toml` per `stage5_codebase_pin.md` adaptation surface, commit on top of pinned SHA. | code_implementer | code_implementer | day 1 | Upstream cloned cleanly + one `Stage 6 adaptation:` commit on top of pinned SHA + `git status --short` empty + `stage6_implementation_receipt.md` written with `path_taken: pin` and the runnable entrypoint command (`PYTHONPATH=src python -m sample.benchmark --smoke --seed 42`). |
| T1 | Submit a remote H100 run of `PYTHONPATH=src python -m sample.benchmark --smoke --seed 42` via `fast_submit.sh` (default `gpu: H100:1`), validate the smoke run's RESULT_JSON envelope (n_problems=3, both strategies present), then submit the full `--full --seed 42` run on the same H100 (n_problems=10). Capture the final RESULT_JSON, write `stage6_experimentalist.md` populated with the run_ids, run_command, stdout tails, parsed `accuracy_direct` / `accuracy_cot` / `direct_truncated` / `cot_truncated` / `n_problems`, and the H1 verdict (`paired_diff ≥ 0.20`). | experiment_runner | experiment_runner | day 1 | A valid `RESULT_JSON` block appears in the runner report; full-run `accuracy_cot - accuracy_direct ≥ 0.20` (H1 supported). |

## Dependencies

T1 depends on T0 (smoke run cannot start until `benchmark.py` exists,
is committed, and is pushed to the remote working dir).

## Risk register

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Session budget exhausted on remote infra | medium | If `fast_query_budget.sh` returns `$0`, the runner reports `BLOCKED: budget` honestly — the engine's hard-gate work in PR #66 ensures this surfaces correctly rather than auto-passing an empty stage. |
| Model path `/mnt/data0/hf_models/Qwen/Qwen2.5-7B-Instruct` missing on remote | low | The runner reports `BLOCKED: model_path_missing`. The reference `qwen_inference_test.py` shipped with the runner skill confirms this path exists on the standard H100 image. |
| Upstream SHA `621e4974ca…` is unreachable | low | The runbook tells 6a to fall back to the latest reachable commit on `main` (and document the deviation in the receipt). |
| Claude Opus skips Phase 5 (writes patches, forgets receipt) | medium (observed in earlier runs) | Engine hard-gate from PR #66 catches this and retries 6a with an explicit "you skipped the receipt" feedback. |
| `torch.cuda.is_available()` returns False on the assigned host | low | `benchmark.py` raises `RuntimeError("CUDA required")` — runner classifies as `BLOCKED: no_gpu` and surfaces to the critic honestly. |
| CoT generation truncates before "The answer is N." | medium | `max_new_tokens=256` for CoT (~5× typical reasoning length for these problems); `extract_int` falls back to the last integer literal so partial CoT still scores when arithmetic is correct. |
