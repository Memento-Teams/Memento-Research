# Stage 3 — Idea & Hypothesis

## Single hypothesis (H1, primary)

**H1**: On a 10-problem hand-curated GSM-style word-problem benchmark,
**chain-of-thought prompting** (system prompt asking the model to
reason step by step) achieves at least **20 percentage points** higher
accuracy than **direct-answer prompting** (system prompt asking for
only the final integer) when both are evaluated on **Qwen2.5-7B-Instruct**
with greedy decoding on H100.

## Rationale

The "chain-of-thought" effect is one of the most reliably reproducible
phenomena in instruction-tuned LLM evaluation: forcing the model to
emit intermediate reasoning tokens before committing to a final answer
materially improves arithmetic accuracy at the 7B scale. GSM-style
problems with two-step arithmetic (e.g. "3 packs × 12 pens, give 8
away — how many left?") routinely defeat direct-answer prompting
because the model jumps to the *last quantity mentioned* instead of
computing the actual remainder.

Expected accuracies on these 10 problems with greedy decoding:

- **direct** (max_new_tokens=16, "answer with only the integer"): ~0.4–0.7
- **cot**    (max_new_tokens=256, "think step by step, then answer"): ~0.8–1.0

A 20 pp gap is the minimum claim that's robustly true for Qwen2.5-7B on
this dataset; we expect the actual gap to be larger (≥0.3) and use the
loose threshold to absorb kernel-level non-determinism.

## Why a single hypothesis

This fixture's role is to validate the Stage 6 pipeline (6a → hard-gate
→ 6b → critic) **with real model inference on GPU**, not to publish a
paper. A single, easily-evaluable claim minimises the implementation
surface (~140 LOC for 6a) while still exercising the same data-flow
shape (paired accuracy per strategy → `RESULT_JSON` envelope) the
runbook's smoke-quality gate already validates against.

H2 ("does the gap close as model size grows?") and H3 ("does CoT help
on non-arithmetic tasks?") would be appropriate for a real study but
are deliberately out of scope to keep total wall-clock under three
minutes on a single H100.
