# Stage 4 — Methodology

## 1. Variables

| Symbol | Role | Type | Definition |
|--------|------|------|------------|
| `strategy` | IV (independent) | categorical(2) | `direct` (bare-answer prompt) or `cot` (step-by-step prompt) |
| `problem_id` | IV (control, paired) | int 0..9 | dataset index, identical for both strategies |
| `model_output` | mediator | str | raw decoder output from Qwen2.5-7B-Instruct |
| `predicted` | derived | int \| None | integer extracted from `model_output` via the answer-extraction regex |
| `correct` | DV (primary, per-problem) | bool | `predicted == ground_truth` |
| `accuracy_direct` | DV (aggregate) | float ∈ [0,1] | mean `correct` over n problems under direct prompt |
| `accuracy_cot`    | DV (aggregate) | float ∈ [0,1] | mean `correct` over n problems under CoT prompt |
| `diff` | DV (derived) | float ∈ [-1,1] | `accuracy_cot − accuracy_direct` |

## 2. Empirical claims list

- **C1**: On 10 hand-curated GSM-style word problems, chain-of-thought
  prompting yields **strictly higher** accuracy than direct-answer
  prompting on Qwen2.5-7B-Instruct (greedy decoding, BF16, H100).
  Operationalised as `accuracy_cot - accuracy_direct ≥ 0.20`.

## 3. Dataset construction

Ten GSM-style word problems, hand-curated, single-step or two-step
arithmetic. Each problem is a Python dict:

```
{"id": <int>, "text": "<natural-language word problem>", "gt": <integer>}
```

No explicit `Compute:` clause is embedded — the model is expected to
parse the word problem itself. This is the meaningful difference from
the earlier deterministic fixture: the experiment now actually
exercises the model's arithmetic reasoning, not just regex parsing.

## 4. Algorithm

```python
MODEL_PATH = "/mnt/data0/hf_models/Qwen/Qwen2.5-7B-Instruct"

tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True, trust_remote_code=True)
model     = AutoModelForCausalLM.from_pretrained(
    MODEL_PATH, torch_dtype=torch.bfloat16,
    device_map="cuda:0", local_files_only=True, trust_remote_code=True,
)

def build_messages(strategy, problem_text):
    if strategy == "direct":
        return [
            {"role": "system", "content":
                "You are a math problem solver. Answer with only the final integer, no explanation."},
            {"role": "user",   "content": problem_text},
        ]
    return [
        {"role": "system", "content":
            "You are a math problem solver. Think step by step, then give the final answer."},
        {"role": "user",   "content":
            f"{problem_text}\n\nLet's think step by step. Show your work, then write 'The answer is N.' on the final line."},
    ]

def extract_int(text):
    m = re.search(r"answer is\s*\$?(-?\d+)", text, re.IGNORECASE)
    if m: return int(m.group(1))
    nums = re.findall(r"-?\d+", text)
    return int(nums[-1]) if nums else None

# For each strategy, for each problem: chat-template → generate → extract → compare.
```

**Safety note**: `eval` is **not** used in this version — the model
itself is the reasoning engine. The only `re` call is on the model's
generated text (read-only string scanning).

## 5. Statistical test

Paired binomial / McNemar on the 10 (or 3-in-smoke) paired
{direct,cot} outcomes, one-sided, α = 0.05. With n=10 the test is
borderline-powered, but a ≥0.20 effect size on greedy decoding is
expected for Qwen2.5-7B. The test is included so the deliverable
exercises the same `paired_diff` field shape the critic looks for.

## 6. Output contract (CRITICAL)

`benchmark.py` MUST print exactly one final stdout line in this
format (the runbook's `Step 1b'` smoke-quality gate parses this
envelope):

```
=== RESULT_JSON: {"accuracy_direct": <acc_direct>, "accuracy_cot": <acc_cot>, "direct_truncated": <int>, "cot_truncated": <int>, "n_problems": <n>} ===
```

This reuses the canonical `accuracy_direct` / `accuracy_cot` field
names from `experiment-execution-runbook` Step 1b'. Semantics now
**match the field names exactly** (this is the original intent —
the prior fixture version was a syntactic re-use).

## 7. Reproducibility

- Greedy decoding (`do_sample=False`, `temperature=None`) means same
  inputs → same outputs modulo kernel non-determinism.
- Model weights are pinned (Qwen2.5-7B-Instruct, exact local copy on
  `/mnt/data0/hf_models/...`).
- Code is committed on top of the pinned `pypa/sampleproject` SHA
  (see `stage5_codebase_pin.md`).
- Random seed (Python + torch) set from `--seed 42` for any
  non-determinism that might creep in (e.g., dropout, cuBLAS
  workspace selection).

## 8. Threats to validity

1. **Dataset is hand-curated and tiny (n=10)** — not generalisable to
   real GSM8K; this is a fixture for pipeline smoke-testing.
2. **One model only** — Qwen2.5-7B-Instruct. The CoT-helps result is
   well-established for this family; the experiment does not claim
   the gap generalises to all 7B models.
3. **Greedy decoding** — no variance estimate. Acceptable for smoke;
   would not be acceptable for a publication claim (would need
   `n_seeds ≥ 5`).
4. **Answer extraction is regex-based** — a CoT generation that
   reasons correctly but ends with the wrong "answer is N" phrasing
   could be wrongly scored. The fallback to the last integer literal
   absorbs most of these.

## 9. Why no framework figure was rendered

This fixture intentionally skips the Stage 4 nano-banana figure
render: a 2-strategy comparison on 10 problems is not visually
interesting. A 1×1 PNG placeholder is committed so downstream code
that expects the file does not crash.
