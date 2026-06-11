# Stage 1 — Topic Refinement

**Refined topic**: A 10-problem GSM-style arithmetic-reasoning smoke
benchmark comparing two **prompting strategies** — bare direct-answer
prompting vs chain-of-thought prompting — on Qwen2.5-7B-Instruct
running on H100.

## Research question

When evaluating a simple arithmetic-word-problem benchmark
(GSM-style) with a 7B instruction-tuned LLM, how much accuracy do
we gain by **prompting the model to think step by step** (Strategy B,
CoT) versus **demanding a bare integer answer with no reasoning**
(Strategy A, direct)?

## Scope

- **In scope**: paired comparison of two prompting strategies on 10
  hand-curated GSM-style word problems, all evaluated on
  Qwen2.5-7B-Instruct (BF16, greedy decoding, H100 GPU). Same model
  weights, same problems, same decoding kwargs — only the system
  prompt and `max_new_tokens` differ.
- **Out of scope**: model-size scaling, full GSM8K's 1,319-problem
  suite, multi-shot / few-shot prompting, agentic reasoning loops,
  non-arithmetic task domains.

## Why this is useful as a smoke test

This topic is deliberately tiny: the full evaluation runs in under
three minutes on a single H100, the implementation is ~140 LOC of
Python (`transformers` + a regex), and the model weights are already
on the host's local disk at `/mnt/data0/hf_models/Qwen/Qwen2.5-7B-Instruct`.

It exists to exercise the Stage 6 pipeline (6a code-writer →
hard-gate → 6b runner → critic) end-to-end **including real model
inference on GPU** — not just deterministic CPU computation. The two
strategies mirror the kind of "naive baseline vs principled approach"
comparison that real benchmark papers run, so the pipeline exercises
the same shape of output (RESULT_JSON with `accuracy_direct` /
`accuracy_cot` / `direct_truncated` / `cot_truncated` / `n_problems`)
the runbook's smoke-quality gate already understands — with the
field names now carrying their literal semantic meaning.
