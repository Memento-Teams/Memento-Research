# Stage 5 — Upstream Codebase Pin

## Primary upstream

- **Repository**: `https://github.com/pypa/sampleproject`
- **Commit**: `621e4974ca0708e6a4ddec3e2f5da50d56e6a1bb` (latest stable on `main` at fixture authoring time; Stage 6 may resolve to whichever SHA `git fetch` returns if the exact commit is unreachable — document the deviation in the receipt).
- **License**: MIT
- **Test command**: `python -m pytest -q` (the upstream ships an empty test scaffold; this should exit 0 trivially after install)

## Why this repo

`pypa/sampleproject` is the canonical Python packaging template
(~50 LOC of real code in `src/sample/__init__.py`). It is small,
public, MIT-licensed, dependency-free, and *deliberately empty* —
the perfect host for a tiny new benchmark module that loads a real
LLM on GPU.

This fixture exists to exercise the Stage 6 pipeline end-to-end —
including **real Qwen2.5-7B inference on H100** — in under ten
minutes. The pin choice reflects that.

## Adaptation surface (what we change, with exact paths)

| File | Change | Reason | Estimated LOC |
|------|--------|--------|---------------|
| `src/sample/benchmark.py` *(new)* | Implement the GSM-style 10-problem LLM benchmark: `DATASET` constant (10 hand-curated GSM-style word problems each with a `text` and integer `gt`), load `Qwen2.5-7B-Instruct` from `/mnt/data0/hf_models/Qwen/Qwen2.5-7B-Instruct` via `transformers`, run **two prompting strategies** (direct-answer vs chain-of-thought) for each problem, extract the last integer from each generation, score per-strategy accuracy, compute `paired_diff`, and print the canonical `=== RESULT_JSON: ... ===` envelope. | The entire experiment. | ~140 |
| `pyproject.toml` | Add `gsm-llm-benchmark = "sample.benchmark:main"` console-script entry under the existing `[project.scripts]` block so the smoke runner can invoke the entrypoint from PATH. | Make the entrypoint discoverable; matches the `Runnable entrypoint` field of the receipt template. | 1 |

**Total adaptation surface: ~141 LOC across 2 files.**

Files **NOT** to touch (use as-is):
- `src/sample/__init__.py` — keep the package skeleton untouched
- `tests/` — the upstream's empty test scaffold
- `README.md`, `LICENSE.txt`, `setup.py` — upstream metadata
- Any other file in the upstream tree

## Implementation hints for Stage 6a (Claude Opus)

### Model + environment

- **Model path** (already on disk, do **not** download): `/mnt/data0/hf_models/Qwen/Qwen2.5-7B-Instruct`
- **Stack**: `torch` + `transformers` (pre-installed in the infra container; no `pip install` required).
- **Device**: `cuda:0` if `torch.cuda.is_available()` else fail loud — this benchmark **requires** GPU; CPU fallback is not desired (we want to actually exercise the GPU).
- **dtype**: `torch.bfloat16` on GPU.
- **Load kwargs**: `trust_remote_code=True`, `local_files_only=True`. Same as the reference `qwen_inference_test.py` shipped with the runner skill.
- **Chat template**: use `tokenizer.apply_chat_template(..., add_generation_prompt=True)`. Do NOT hand-roll prompt formatting.
- **Generation kwargs**: `do_sample=False, temperature=None, top_p=None, pad_token_id=tokenizer.eos_token_id`. For direct strategy: `max_new_tokens=16` (just the answer). For CoT strategy: `max_new_tokens=256` (room to reason).

### Two strategies

**A — direct** (system prompt encourages immediate answer, no reasoning):
```
system: "You are a math problem solver. Answer with only the final integer, no explanation."
user:   "<problem.text>"
```

**B — cot** (system prompt encourages step-by-step reasoning):
```
system: "You are a math problem solver. Think step by step, then give the final answer."
user:   "<problem.text>\n\nLet's think step by step. Show your work, then write 'The answer is N.' on the final line."
```

### Answer extraction

After generation, run the model's output text through:

```python
import re
def extract_int(text: str) -> int | None:
    # Prefer an explicit "answer is N" pattern when present.
    m = re.search(r"answer is\s*\$?(-?\d+)", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # Otherwise fall back to the last integer literal in the output.
    nums = re.findall(r"-?\d+", text)
    return int(nums[-1]) if nums else None
```

### Hypothesis to exercise

H1 (chain-of-thought helps): `accuracy_cot - accuracy_direct ≥ 0.20`.

Qwen2.5-7B-Instruct is strong enough that we expect `accuracy_cot ≈ 0.8-1.0` and `accuracy_direct ≈ 0.4-0.7` on these 10 problems, giving a paired diff of ≥0.2 with high probability. This is **not** deterministic (greedy sampling is, but small numerical variance from kernel non-determinism is possible), so the threshold is loose enough to absorb that noise.

### Sample problems (use these 10 verbatim)

```python
DATASET = [
    {"id": 1,  "text": "Janet has 24 apples. She gives 7 to her brother and buys 5 more. How many apples does Janet have now?", "gt": 22},
    {"id": 2,  "text": "A train travels 60 miles in 1.5 hours. What is its average speed in miles per hour?", "gt": 40},
    {"id": 3,  "text": "A rectangle is 8 cm wide and 12 cm long. What is its perimeter in centimeters?", "gt": 40},
    {"id": 4,  "text": "Sam reads 25 pages per day. How many pages will Sam read in 4 days?", "gt": 100},
    {"id": 5,  "text": "Maria buys 3 packs of pens with 12 pens per pack. She gives 8 pens to friends. How many pens does she have left?", "gt": 28},
    {"id": 6,  "text": "A water tank holds 200 liters. It is currently 3/4 full. How many liters of water are in the tank?", "gt": 150},
    {"id": 7,  "text": "Tom is 4 times as old as his sister. If his sister is 7 years old, how old is Tom?", "gt": 28},
    {"id": 8,  "text": "A store sells shirts for $15 each. If Alex buys 3 shirts and pays with a $50 bill, how much change does Alex get?", "gt": 5},
    {"id": 9,  "text": "Lisa runs 5 km on Monday, 7 km on Tuesday, and 3 km on Wednesday. What is her total distance in km?", "gt": 15},
    {"id": 10, "text": "A school has 8 classrooms with 25 students each. How many students are in the school in total?", "gt": 200},
]
```

## Smoke command

`PYTHONPATH=src python -m sample.benchmark --smoke --seed 42`

The `--smoke` flag runs on **the first 3 problems** (so the runner's
Step 1b' quality gate can do a fast sanity-check before committing to
all 10). The `--full` flag (or absent `--smoke`) runs all 10.

## Output contract

`benchmark.py` MUST print exactly one final line to stdout in this
format (the runbook's `Step 1b'` smoke-quality gate parses this
envelope):

```
=== RESULT_JSON: {"accuracy_direct": <acc_a>, "accuracy_cot": <acc_b>, "direct_truncated": <int>, "cot_truncated": <int>, "n_problems": <n>} ===
```

`direct_truncated` / `cot_truncated` count generations that hit
`max_new_tokens` without emitting a `</s>` / EOS — informational.

## Vendoring policy

Stage 6 will:

1. `git clone --depth 1 https://github.com/pypa/sampleproject upstream` into the project workspace.
2. `cd upstream && git fetch --depth 1 origin <commit_sha> && git checkout <commit_sha>` to lock the SHA.
3. Add `src/sample/benchmark.py` and edit `pyproject.toml` per the adaptation surface table.
4. `git add -A && git commit -m "Stage 6 adaptation: GSM LLM benchmark on Qwen2.5-7B"` on top of the pinned SHA.
5. Write `stage6_implementation_receipt.md` with `path_taken: pin`, the entrypoint `PYTHONPATH=src python -m sample.benchmark --smoke --seed 42`, and the `git log <pin_sha>..HEAD --oneline` summary.

## Failure mode handling

- Pretest is the upstream's empty test scaffold — `pretest_status` should
  be `PASSED` on Linux (after `pip install -e .` or with `PYTHONPATH=src`)
  and `SKIPPED_ENV` on macOS (`No module named 'sample'`).
- If the model path does not exist on the remote host, the runner should
  report `BLOCKED: model_path_missing` and stop — there is no fallback
  model for this fixture (it's deliberately tied to the pre-loaded
  Qwen2.5-7B-Instruct).
- If `torch.cuda.is_available()` returns False, fail loudly with
  `RuntimeError("CUDA required; this is a GPU benchmark")` — do NOT
  silently fall back to CPU (defeats the smoke purpose).
