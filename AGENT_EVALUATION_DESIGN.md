# Agent Evaluation Design — Deterministic-First, Two-Tier

> **The problem this solves.** Today every one of the 9 pipeline stages is "producer LLM writes → critic LLM grades PASS/REJECT." The critic hallucinates too, and 5 of 9 stages (S1/S2/S3/S8/S9) have no fixed rubric — the critic *self-invents* its scoring dimensions each run. That is why it feels messy and untested: **it is an LLM checking an LLM, with nothing objective underneath.** And separately, **every PR that changes an agent ships with only mock unit tests** — nothing proves the change actually improved (or didn't regress) that agent's behavior.
>
> This document designs both layers:
> - **Tier A — Runtime evaluation:** how each *agent* is evaluated when the pipeline runs.
> - **Tier B — PR evaluation:** how each *PR* that touches an agent is evaluated before merge.
>
> Companion: `AUTORESEARCH_EVALUATION_MAP.md` (the diagnosis this builds on).

---

# TIER A — Runtime agent evaluation

## A.1 The principle

**A stage cannot PASS on an LLM's say-so for anything a machine can decide.** Gates run in this order, and the order *is* the point:

1. **Deterministic hard-gates run FIRST, before the LLM critic is dispatched.** Missing file, fabricated `run_id`, verdict over-claiming past its evidence, non-English → REJECTED by code. The LLM critic never sees it. Saves a cycle *and* removes the LLM's ability to wave through an objectively broken artifact.
2. **The LLM is a labeled, separate, advisory judge — never disguised as a test.** It adjudicates only the irreducible semantic residue ("is this RQ genuinely falsifiable", "does this cited paper actually support this claim"). Its output is recorded as `llm_judgement`, not `gate_pass`. A low-confidence LLM PASS is downgraded to hold-for-CEO.
3. **Every stage emits machine-checkable metrics on every attempt** — numbers in a queryable table, not prose in a transcript.

The pathology this kills (eval-map rank-7): the same S1 doc re-PASSed v1–v4 at drifting confidence 0.85/0.87/0.88/0.87 with no deterministic assertion underneath. Under this framework the doc passes the same deterministic checks every time (stable) or fails them every time (caught) — **the confidence float stops being the decision-maker.**

## A.2 The 3-layer model (uniform across all 9 agents)

| Layer | What it is | Blocks? | Lives where | Example |
|---|---|---|---|---|
| **L1 — Deterministic hard-gate** | Byte-decidable facts: pure functions over artifacts + cross-stage set algebra + external ground-truth (infra API, arXiv, roster). | **YES** — REJECT/hold *before* `_dispatch_critic`. | engine pre-critic block (sibling of the 6a→6b gate); `stage_validators/stageN.py` | S6: every `run_<hex>` resolves on the infra API with claimed status+cost |
| **L2 — Deterministic advisory metric** | The "test suite": numbers computed deterministically but too contextual to auto-reject on. Logged every attempt. | **NO** — logged + threshold-flagged; may force hold. | same validators → metrics table | S2 `citation_grounding_rate`; S3 `topic_overlap` |
| **L3 — Semantic judgement (LLM)** | The irreducible residue: meaning, soundness, novelty, "most material defect." | **Advisory + independent** of the producer; confidence floored. | existing `*-quality-critic` skills, labeled `llm_judgement` | S4 "is N=20 underpowered?" |

**The anti-regression rule:** anything currently in L3 that is actually machine-decidable **must be promoted to L1/L2.** The S4 framework-figure gate (declared "non-negotiable," yet 4/5 real runs passed with no PNG on disk) is the canonical example of L3 theater → becomes L1 (PNG magic-bytes + IHDR + embed-reference check).

**L3 mitigation (the LLM can't be removed, so make it trustworthy):**
- **Independence** — break the S9 producer≡critic=00013 circularity (assign a 2nd adversarial-capable employee; assert `producer_id != critic_id`).
- **External reviewer** — cspaper.org review (from PR #108) becomes the *external* L3 layer for S9, kept separate from the pipeline's own critics.
- **Calibration** — log `(reported_confidence, structural_correct)` so L3's reliability is itself measured (Brier residual), not trusted.

## A.3 Per-stage table — top L1 hard-gate(s) + key L2 metric

`[exists]` = already in engine · `[build]` = must build · `[infra]` = needs external call

| Stage | Top L1 hard-gate(s) | Key L2 metric | Status |
|---|---|---|---|
| **S1 Topic** | Pre-reg contract sections present (RQ/H0/H1/numeric-MDE/decision-rule/scope); MDE number byte-equal between H1 line and success-criteria table (threshold-equality) | `confidence` vs floor 0.80 → hold; `nonstub_bytes` | `[build]` validator |
| **S2 Literature** | Citation existence rate via batched arXiv API; every body cite ∈ References | `citation_grounding_rate`; `ref_vs_unique_id_count` | `[build]` `[infra]` |
| **S3 Idea** | Every `arxiv:XXXX#cYY` resolves in aigraph corpus (`127.0.0.1:8765/aigraph/query/graph?ids=…`, offline fallback = membership in corpus jsonl); REJECT on zero-resolution | `grounding_rate`; `topic_overlap` Jaccard vs S1 | `[build]` `[infra]`; shape-guard `[exists]` |
| **S4 Methodology** | Framework PNG exists + valid (magic+IHDR+size) + embedded w/ numbered caption; pre-reg/locked-decision-rule section present | `power_token_classes_hit` (≥3 of α/β/MDE/ICC/test); CJK-ratio | `[build]` |
| **S5 Experiment** | Every task-row assignee resolves to a real roster `employee_id` (`load_employee_configs()`); 3 deliverable files present + non-trivial | `pct_criteria_machine_verifiable`; `remote_has_experiment_runner` | `[build]`; loader `[exists]` |
| **S6 Auto-Exp** | **`run_id` infra verification**: every `run_<hex>` exists on infra API w/ matching status+cost; succeeded ⇒ non-degenerate RESULT_JSON (0<acc<1, trunc<0.5). REJECT before critic | `plausibility_band_delta` vs S2 baseline; `runner_row_coverage` | 6a→6b `[exists]`; **infra gate `[build]` `[infra]`** |
| **S7 Result** | Verdict ordinal ≤ S6 coverage ceiling (no data ⇒ only `INCONCLUSIVE_DUE_TO_COVERAGE`); cited `run_id`s ⊆ S6 set; pre-reg test tokens string-appear in S4/S5 (anti-HARK) | `harK_invented_tokens`; `verdict_ordinal` vs `ceiling` | `[build]` pure-parse |
| **S8 Paper** | **main.tex compiles to non-empty PDF** + Figure 1 renders; S8 `run_id`s ⊆ S6; S8 verdict ordinal ≤ S7 | `untraceable_results_numbers` (=0); `undefined_citations` | `[build]`; tool `[exists]` |
| **S9 Self-Review** | **Producer ≠ critic** (roster lookup); verdict capped at real S6/S7 ceiling; cited `run_id`s ⊆ S6 | `(confidence, structural_correct)` calibration; `cited_artifacts_exist` | `[build]`; needs roster change |
| **Engine-wide** | Fix `gate_review_stage{N}.md` naming bug (dead fallback today); no-critic → REJECT not auto-PASS; per-stage confidence floor | `gate_file_present`; `confidence<floor` flag | `[build]` ~15 LOC |

## A.4 The shared substrate — *this is how you fix "it's messy / no tests"*

`record_stage_episode` (`research_memory.py:153`) already logs `passed, confidence, retries, reward, outcome` per attempt. **Extend its record dict with a `metrics` object** keyed by `(project_id, stage_id, attempt)`. No new store — append fields to the existing JSONL.

```python
record["metrics"] = {
    "gates": {                       # L1 booleans — the deterministic verdict
        "runid_infra_verified": True, "runid_subset_of_s6": True,
        "result_json_nondegenerate": True, "english_ok": True, "gate_file_present": True,
    },
    "advisory": {                    # L2 numbers — the "test suite"
        "citation_grounding_rate": None, "corpus_grounding_rate": 1.0,
        "topic_overlap": 0.71, "power_token_classes_hit": 4,
        "plausibility_band_delta_pp": -23.0, "untraceable_results_numbers": 0,
        "verdict_ordinal": 2, "ceiling_ordinal": 2,
    },
    "llm_judgement": {               # L3 — explicitly labeled judgement, not a test
        "verdict": "PASS", "confidence": 0.87, "floor": 0.80, "downgraded_to_hold": False,
    },
}
```

**Keystone property:** once every attempt writes this, pipeline health becomes a query:

```bash
# which stage fails which gate most? where does confidence drift while gates stay green (rank-7)?
jq -r 'select(.metrics.gates|to_entries|any(.value==false))
       | [.stage_id, .retries, (.metrics.gates|to_entries|map(select(.value==false)|.key)|join(","))]|@tsv' \
  .onemancompany/**/stage_memories.jsonl | sort | uniq -c | sort -rn
```

## A.5 Rollout order — most integrity per unit effort

| # | Gate | Why first | Effort | PR tie-in |
|---|---|---|---|---|
| **1** | **S6 `run_id` infra verification** (L1) | THE decisive check; S6b is rank-1, every S7/S8/S9 claim inherits these run_ids, fabrication is the cardinal sin. Reuses `fast_query_exp_status.sh`; sibling of 6a→6b gate. | ~60 LOC | **#107** — land verification *on top of* its run_tracker |
| **2** | **Metrics substrate** (A.4) | Every other metric has nowhere to land without it; opaque → queryable. | ~30 LOC | **#86** exposes it; **#105** keeps pipeline alive to collect rows |
| **3** | **S8 terminal-artifact gate** (compile-to-PDF + Fig-1 + run_id-subset + verdict-ordinal) | Closes the headline gap: 9 PASSes can yield no readable paper. Reuses `tools/md_to_neurips_pdf.py`. | ~80 LOC | **#104** — wire as post-producer check |
| **4** | **Engine-wide hygiene** (naming-bug fix + no-critic→REJECT + confidence floor) | Cheap, cross-cutting, restores a dead safety net; direct fix for confidence-drift. | ~15 LOC | none |

Then: S5 roster-validation → S2 citation verifier → S3 corpus-resolution → S7/S9 verdict-≤-coverage → **S9 independence** (roster change; cspaper as external L3 reviewer).

---

# TIER B — PR-level evaluation pipeline

> Yes — **every PR that touches an agent should run an evaluation pipeline**, but a *tiered* one, because running the full 9-stage pipeline costs LLM tokens (and sometimes H100 time). The trick: **reuse Tier-A's deterministic metrics as the objective yardstick** so PR eval isn't "reviewer's subjective opinion" (which would just be LLM-judging-LLM again). A change is "good" iff the affected stage's deterministic metrics improve or hold — measured, not vibed.

## B.1 Why current PR review is insufficient

CI today = `pytest tests/` + version-bump gate + human review. For an agent-behavior PR that misses the point entirely:
- **#104** (Stage-8 chunking) — only mock unit tests; *never ran a real paper* to prove it beats the timeout without quality loss.
- **#107** (Stage-6 long runs) — no long experiment actually collected; *and shipped a blocker* (`producer_b_waiting` missing from `_ACTIVE_PHASES`) that 6 happy-path tests didn't catch.
- **#108** (eval-agent) — the evaluator itself was never evaluated.

The shared failure: **tests cover the code, not the agent's behavior change.**

## B.2 The 3-level PR eval ladder (cost-gated by what the PR touches)

| Level | Runs | Cost | Trigger | Gate |
|---|---|---|---|---|
| **L0 — Contract check** | Static, deterministic, no LLM: which stage(s) the diff touches → assert that stage's **artifact contract** still holds (the Tier-A `stage_validators/stageN.py` run against golden fixture artifacts), its L1 gates still pass on the fixtures, version monotonic, the new code's tests exist & pass. | **free, ~seconds** | **every PR** (auto, in CI) | **hard — blocks merge** |
| **L1 — Single-stage replay** | Replay *only the changed agent* on a frozen input fixture (prior stages' outputs are pinned on disk), compute that stage's Tier-A L2 metrics, diff before/after. | **1 stage of LLM, ~$** | PR touching a stage producer/critic/skill (path-based) | **advisory — posts a metrics delta table; reviewer/maintainer decides** |
| **L2 — End-to-end** | Full 1→9 on a fixed benchmark topic; compare the whole metric vector + does a compilable PDF come out. | **full pipeline, $$ (+H100 if S6)** | opt-in label `full-eval`, or scheduled nightly on `main` | **advisory + tracked over time** |

**Decision rule (what makes a PR "pass eval"):** L0 must be green (hard). For L1/L2: **no Tier-A deterministic metric for the touched stage regresses** (e.g. a Stage-8 PR must not drop `pdf_compiles` or raise `untraceable_results_numbers`; a Stage-3 PR must not lower `grounding_rate`). This is the objective standard that replaces "looks fine to me."

## B.3 The fixtures (the missing asset)

PR eval needs **frozen golden artifacts** — there are none today. Build a small `eval/fixtures/` set:
- One **frozen project workspace per stage boundary**: `stage{1..N-1}_*.md` pinned, so replaying stage N has deterministic inputs. (Source them from the two exemplar runs already parked: `965257e794dd` success, `415335751124` stalled-at-S6.)
- A tiny **benchmark topic** that's cheap to run end-to-end (small N, smoke-sized experiment) for L2.
- **Golden metric baselines** committed alongside, so L1/L2 diff against a known-good vector.

## B.4 How it wires into CI

New `.github/workflows/agent-eval.yml`:
- **L0** runs on every PR (a new `pytest eval/contract/` suite invoking `stage_validators/*` against `eval/fixtures/`). Path-independent, hard gate.
- **L1** runs when the diff touches `src/onemancompany/**/pipeline_engine.py`, a `*/skills/*/SKILL.md`, or `default_skills/**` — posts a PR comment with the before/after metric delta. Needs a CI-available model key (cheap model OK for the replay; the *gate* is on deterministic metrics, not the model's prose).
- **L2** runs only on the `full-eval` label or nightly — uploads the metric vector + PDF artifact.

## B.5 Relationship between the two tiers (the key synergy)

```
Tier A (runtime)  ── defines ──▶  the deterministic metrics  ◀── consumes ──  Tier B (PR eval)
   per-stage L1/L2 checks            (grounding_rate,                the yardstick: "did the
   + the metrics substrate            run_id_verified,                changed stage's metrics
   (A.4) is the single                pdf_compiles, …)                regress?" — objective,
   source of truth                                                    not reviewer opinion
```

**Build Tier-A first (especially the A.4 substrate and the 4 rollout gates).** Tier B is then mostly *plumbing* — replay harness + fixtures + a CI workflow — because the hard part (deciding *what good looks like, deterministically*) is already solved by Tier A. Building Tier B before Tier A would force PR eval back onto subjective LLM judgement, reintroducing the exact problem.

---

## Appendix — provenance

Generated from a 9-stage parallel design workflow (one designer + one adversarial challenger per stage, verifying each proposed check is *truly* deterministic and runnable today, + a synthesis pass), grounded in `AUTORESEARCH_EVALUATION_MAP.md` and the deployed engine. Key files: `pipeline_engine.py` (6a→6b gate ~1157, `_parse_critic_pass` 1661, naming bug 1667/1677, `_find_employee_by_skill` 311), `research_memory.py:153` (`record_stage_episode` — the substrate extension point), `tools/md_to_neurips_pdf.py` (S8 compile, reuse), `config.py::load_employee_configs` (roster, S5/S9).
