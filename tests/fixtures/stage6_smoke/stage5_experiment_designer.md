# Stage 5 — Experiment Plan

## 1. Setup

- **Codebase**: pinned in `stage5_codebase_pin.md` (pypa/sampleproject, MIT).
- **New module**: `src/sample/benchmark.py` (~140 LOC) added by Stage 6a.
- **Model**: `Qwen2.5-7B-Instruct` pre-loaded at
  `/mnt/data0/hf_models/Qwen/Qwen2.5-7B-Instruct` on the H100 host.
- **Hardware**: 1× H100 (the runner's `fast_submit.sh` `base.conf.json`
  default — `"gpu": "H100:1"`).
- **Dataset**: 10-row constant inside `benchmark.py`. Each row is
  `{"id": int, "text": "<GSM-style word problem>", "gt": int}`.
- **Strategies**:
  - **A (direct)**: system prompt asks for the bare integer answer, `max_new_tokens=16`.
  - **B (cot)**: system prompt asks for step-by-step reasoning ending with `"The answer is N."`, `max_new_tokens=256`.
- **Scorer**: extract integer via `re.search(r"answer is\s*\$?(-?\d+)", text)` first, fall back to last integer literal; compare against `gt`; aggregate to per-strategy accuracy.

## 2. Procedure

```python
def evaluate(strategy: str, problems):
    correct, truncated = 0, 0
    for problem in problems:
        messages = build_messages(strategy, problem["text"])
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = tokenizer([prompt], return_tensors="pt").to(model.device)
        max_new = 16 if strategy == "direct" else 256
        with torch.inference_mode():
            out = model.generate(
                **inputs, max_new_tokens=max_new,
                do_sample=False, temperature=None, top_p=None,
                pad_token_id=tokenizer.eos_token_id,
            )
        new_tokens = out[:, inputs.input_ids.shape[-1]:]
        text = tokenizer.batch_decode(new_tokens, skip_special_tokens=True)[0]
        if new_tokens.shape[1] >= max_new:
            truncated += 1
        pred = extract_int(text)
        if pred == problem["gt"]:
            correct += 1
    return correct / len(problems), truncated

acc_direct, trunc_direct = evaluate("direct", DATASET)
acc_cot, trunc_cot       = evaluate("cot",    DATASET)
diff = acc_cot - acc_direct
```

## 3. Smoke vs full

- `--smoke` runs the first 3 problems only — Step 1b' quality gate
  validates the entrypoint works on GPU + the RESULT_JSON envelope
  parses, without committing the full ~2-3 min generation budget.
- `--full` (or absent `--smoke`) runs all 10 problems.

Expected timing on H100:
| Phase | Duration |
|-------|----------|
| Model load (BF16 7B from local disk) | ~10-15 s |
| Smoke: 3 problems × 2 strategies (≈6 generations, up to 272 tokens each) | ~15-30 s |
| Full: 10 problems × 2 strategies (≈20 generations) | ~60-120 s |
| **Total smoke wall-clock** | **~30-45 s** |
| **Total full wall-clock** | **~90-150 s** |

## 4. Output contract

`benchmark.py` MUST print exactly one final line to stdout in this
format (the runbook's `Step 1b'` smoke-quality gate parses this
envelope):

```
=== RESULT_JSON: {"accuracy_direct": <acc_a>, "accuracy_cot": <acc_b>, "direct_truncated": <int>, "cot_truncated": <int>, "n_problems": <n>} ===
```

Field-name mapping:

| Field | This experiment |
|-------|-----------------|
| `accuracy_direct` | Strategy A (direct, max_new_tokens=16) |
| `accuracy_cot`    | Strategy B (CoT, max_new_tokens=256) |
| `direct_truncated` | Count of direct generations that hit `max_new_tokens` |
| `cot_truncated`   | Count of CoT generations that hit `max_new_tokens` |
| `n_problems`      | 3 in smoke mode, 10 in full mode |

## 5. Hypothesis re-statement

H1: `accuracy_cot - accuracy_direct ≥ 0.20` on the 10-problem fixed dataset.

## 6. Decision rule

- H1 holds iff the runner's emitted `accuracy_cot - accuracy_direct ≥ 0.20`.
- Greedy decoding (`do_sample=False`) makes this nearly deterministic;
  small kernel-level non-determinism is absorbed by the 0.20 threshold.

## 7. Sample size / power

n=10 paired is under-powered for a publication claim, and is
acknowledged in the threats-to-validity section of Stage 4. The
fixture is for pipeline validation, not for a research finding —
the expected ≥0.20 pp gap is what makes the test robust despite the
tiny n.

## 8. Threats / exclusions (recap from Stage 4 §7)

This is a fixture for pipeline validation. No external generalisability
is claimed.

- CoT vs direct is exercised on **one** specific 7B instruct model;
  the gap on different model families is not in scope.
- Greedy decoding means we cannot estimate variance — that is
  intentional for a smoke test, where reproducibility >> uncertainty.
- The 10 hand-curated problems are not a held-out test set; they were
  designed to be solvable by a 7B model under CoT.
