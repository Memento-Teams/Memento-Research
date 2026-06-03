# AutoResearch Pipeline — Stage Boundaries & Evaluation Framework

> **Purpose.** Map the 9-stage OneManCompany "AutoResearch" pipeline so we can define an
> evaluation framework: for every stage, *what artifact crosses the boundary*, *what already
> gates it*, and *the single highest-value thing to measure*. Then: where evaluation is
> weakest, what bugs undermine the existing gates, and what to instrument.

## 0. Provenance & scope (read this first)

This analysis is **pinned to the engine actually deployed on 8002** —
`~/onemancompany_8002/src/onemancompany/core/pipeline_engine.py`, **1903 lines**, the
worktree/serve variant that *includes* the MemRL reward machinery
(`_critic_reward`, `_record_stage_memory`, `memory_episodes`, per-stage critic branches for
S3–S7). ⚠️ **Upstream `main` HEAD is a different, ~951-line engine that has NONE of these**
(no `_critic_reward`, no reward clamp, no MemRL, per-stage critic branches only for S4/S5).
Any recommendation below that touches the reward signal or per-stage critic logic must specify
*which engine* — patch the deployed 8002 file, and separately decide whether to upstream.

**Two exemplar runs are referenced (both real, both parked under
`business/projects_parked_20260601-182950/`):**
- **`415335751124`** — an MCTS run that **stalled in Stage 6 execution**; wrote stage1–9 `.md`
  but only `gate_review_stage1..4.md` landed, and only git tags `stage-1/-2/-7`. This is the
  *failure exemplar*.
- **`965257e794dd`** — a **completed run with real data** (CoT accuracy **0.958** vs direct
  **0.208**, run `run_3a0ee2e10075`, $0.08); S7 returned **PARTIALLY CONFIRMED**; gate files
  present through S6/7/8/9. This is the *success exemplar*.

**Producers are resolved by skill, not by hardcoded ID** (`_find_employee_for_stage` →
`_find_employee_by_skill`, which returns the **first** matching employee). The 8002 roster runs
00001–00024. Where two employees carry the same critic skill (e.g. `methodology-quality-critic`
and `adversarial_review` exist on **both 00013 and 00024**), **00013 always wins** and 00024 is
a dead duplicate that is never selected. The S9 producer (`peer_reviewer`) and the critic
(`adversarial_review`) are **both owned only by 00013** → see the circularity finding (§5).

---

## 1. Boundary diagram

```
 raw_topic (self.topic) + prior_context + MemRL guidance
        │
        ▼
 ┌─────────────────────────────┐
 │ S1  Topic Refinement         │  producer skill=topic_refiner   critic=adversarial_review (STUB)
 └─────────────────────────────┘
        │  stage1_topic_refiner.md   (RQ + pre-registered H0/H1 + numeric effect-size Δ + scope)
        ▼
 ┌─────────────────────────────┐
 │ S2  Literature Survey        │  literature_surveyor            critic=adversarial_review (STUB)
 └─────────────────────────────┘
        │  stage2_literature_surveyor.md   (grounded prior work + gap map + stats methodology)
        ▼
 ┌─────────────────────────────┐
 │ S3  Idea Generation          │  idea_generator (aigraph LCG)   critic=adversarial_review + INLINE engine guard
 └─────────────────────────────┘
        │  stage3_idea_generator.md   (primary pilot hypothesis + arxiv:XXXX#cYY-grounded items)
        ▼
 ┌──────────────────────────────────────────────┐
 │ S4  Methodology Design                         │  methodology_designer + debate convener
 │   draft(v1, not graded) → run_debate → revise  │  critic = methodology-quality-critic (REAL rubric)
 │   → stage4_framework_figure.png                │
 └──────────────────────────────────────────────┘
        │  stage4_methodology_designer.md + stage4_debate_transcript.md + stage4_framework_figure.png
        │  (pre-registered design: named design, power α/β/MDE/ICC, primary metric, locked decision rule)
        ▼
 ┌──────────────────────────────────────────────┐
 │ S5  Experiment Design                          │  experiment_designer + debate convener
 │   draft(v1) → run_debate → revise              │  critic = experiment-quality-critic (REAL rubric)
 │   → stage5_assignments.md (dispatch work-tree) │
 └──────────────────────────────────────────────┘
        │  stage5_experiment_designer.md + stage5_assignments.md + stage5_debate_transcript.md
        │  (10-section runnable plan + per-task→employee_id assignment table S6 dispatches from)
        ▼
 ┌──────────────────────────────────────────────┐
 │ S6  Auto Experiment                            │
 │   6a IMPL → stage6_implementation_receipt.md   │  critic = code-quality-critic (REAL, 12 dims)
 │        │                                        │   + ENGINE STRUCTURAL HARD-GATE (deterministic):
 │        ▼  6a→6b structural gate                 │     receipt ≥ ~200B + adaptation patches committed
 │   6b EXEC → stage6_experimentalist.md          │  critic = INLINE exec checklist (NOT a numbered rubric)
 └──────────────────────────────────────────────┘
        │  stage6_experimentalist.md  (real run_<hex> + terminal status + actual_cost + log_tail)
        ▼
 ┌─────────────────────────────┐
 │ S7  Result Analysis          │  result_analyst                 critic = result-quality-critic (REAL rubric)
 └─────────────────────────────┘
        │  stage7_result_analyst.md   (confirmatory results vs pre-reg contract, coverage-capped verdict)
        ▼
 ┌─────────────────────────────┐
 │ S8  Paper Generation         │  paper_writer (+ paper_config)  critic = adversarial_review (NO S8 block)
 └─────────────────────────────┘
        │  stage8_paper_writer.md → main.tex (latex) / render_docx (docx)
        ▼
 ┌─────────────────────────────┐
 │ S9  Self-Review              │  peer_reviewer = 00013          critic = adversarial_review = 00013 (CIRCULAR)
 └─────────────────────────────┘
        │  stage9_peer_reviewer.md
        ▼
   CEO gate (interactive: HOLD for approval · headless/auto-approve: critic-PASS auto-advances)
```

**Two operating modes, very different evaluation strength.** Every stage ends in `phase=gate`
which emits `breakpoint_hit` and **holds for CEO approval** in interactive mode (the critic PASS
is only advisory). In **headless/auto-approve mode the CEO gate is bypassed** and a critic PASS
auto-advances. The framework below must be read with this split in mind — a weak gate is *much*
more dangerous in auto mode.

**Which stages have a real, codified critic rubric:** **S4, S5, S6a, S7** (their
`*-quality-critic/SKILL.md` files exist on 00013). **S1, S2, S3, S8, S9** fall through to the
`adversarial_review` critic, whose `SKILL.md` is a **9-line auto-generated stub** with no
dimensions — the critic *self-invents* its dimension table at review time (non-deterministic),
so any "Dn" shown for those stages is an artifact of one run, not a stable contract. S3 is the
exception: the engine injects an **inline guard** (REJECT if empty / zero hypotheses /
`_No matches_`).

---

## 2. Per-stage table

| Stage | Boundary IN | Deliverable OUT | Critic & hard-gates | Built-in signal | High-value eval point |
|---|---|---|---|---|---|
| **S1 Topic Refinement** | `self.topic` + optional `prior_context` + MemRL S1 guidance. No prior-stage artifact. | `stage1_topic_refiner.md` (RQ, H0/H1, scope, datasets, eval plan, differentiation, risk) + `gate_review_stage1.md` | **STUB critic** (`adversarial_review`/00013). No codified rubric; dimensions self-invented per run. | `_parse_critic_pass` (ambiguous→REJECT) + `_parse_confidence`; reward = clamp(conf−0.15·retries). | Is the RQ **falsifiable, bounded, with a pre-registered H0/H1 and a justified numeric effect-size threshold** — runnable as one decidable experiment? S1 is the contract S4/S5 pre-register against and S7 enforces. |
| **S2 Literature Survey** | S1 deliverable via cumulative `_build_context`. | `stage2_literature_surveyor.md` (foundational papers, benchmark, model, prior evidence, stats methodology, references, handoff) + `gate_review_stage2.md` | **STUB critic**. Only real gate = parseable PASS/REJECT + confidence. | reward = clamp(conf−0.15·retries); git tag `stage-2`. | **Citation integrity + cross-stage grounding**: are cited papers REAL and do the claimed numbers support S1's RQ/design? **No citation-verification tool is wired in** (`citation-verification/SKILL.md` is empty). |
| **S3 Idea Generation** | Cumulative S1+S2; aigraph LCG queries a local arxiv corpus on topic keywords. | `stage3_idea_generator.md` (topic heading, primary pilot hypothesis w/ Grounding line, `# Selected Hypotheses` with `### h…`/`### a…#cr…` arxiv-grounded items) + `gate_review_stage3.md` | STUB critic **+ INLINE engine guard**: REJECT only if empty / 0 hypotheses / `_No matches_`. (G1/G2/G3 "format hard-gates" are an abstraction of this single guard, not a numbered rubric.) | reward = clamp(conf−0.15·retries). | Are hypotheses **genuinely corpus-grounded** (every `arxiv:XXXX#cYY` resolves to a real supporting claim) **vs hallucinated/decorative**, and does the primary pilot stay scoped to S1's model/dataset rather than drifting? |
| **S4 Methodology Design** | S1+S2+S3 read from workspace + MemRL S4. Force-loads `methodology-debate-convener`. | `stage4_methodology_designer.md` + `stage4_debate_transcript.md` + `stage4_framework_figure.png` + `gate_review_stage4.md` | **REAL** `methodology-quality-critic`. **Hard gates: D1–D5** (all must PASS); **D9** non-English auto-REJECT; **D10 framework-figure** missing/unreferenced auto-REJECT (engine line ~780 + skill). Missing transcript = auto-REJECT. | PASS requires all hard gates; D6/7/8 only lower confidence. | Is the design (D3) a **runnable pre-registered contract** — named design + real power math (α/β/MDE/ICC, not naked *n*) + locked decision rule — and does every choice **trace to a named debater** (D8) in the transcript? |
| **S5 Experiment Design** | S4 read in FULL + re-read S1–3 scope + MemRL S5. Force-loads `experiment-debate-convener`. `paper_config` NOT visible. | `stage5_experiment_designer.md` + `stage5_assignments.md` + `stage5_debate_transcript.md` (+ `stage5_codebase_pin.md`) | **REAL** `experiment-quality-critic`. **Hard gates: D1–D5, D8, D10, D12**. Auto-REJECT: any of the 3 files missing, **D10** `<UNASSIGNED>`/missing, **D12** non-English. | Confidence bands 0.90–1.0 / 0.75–0.89 / 0.55–0.74. | Is `stage5_assignments.md` a **dispatchable work-tree** (D10): every executable task → exactly one **real-roster** `employee_id` with a **machine-verifiable** acceptance criterion, explicit deps, risk register, remote tasks → an `experiment_runner`? This is the table S6 literally dispatches from. |
| **S6 Auto Experiment** | **6a**: codebase pin + S4 §2 + S5 §3 + assignments. **6b**: `stage6_implementation_receipt.md` + assignments + remote H100 infra. | **6a** `stage6_implementation_receipt.md` (spec-coverage matrix, smoke+full entrypoints). **6b** `stage6_experimentalist.md` (per-runner run_id/status/cost/metrics/log_tail) | **6a** `code-quality-critic` (12 dims; **hard gates: D1–D5, D11, D12** — D10 is *confidence-reducing only*, non-English its sole auto-REJECT sub-case) **+ deterministic engine 6a→6b structural gate** (receipt non-trivial + patches committed). **6b** **inline checklist** (every assignments row addressed; `experiment_runner` rows need real run_id + terminal status + actual_cost + log_tail; **fabricated/simulated = auto-REJECT**). | 6a→6b gate bounces 6a without burning a 6b cycle. | **Did the code ACTUALLY run** — every `experiment_runner` row has a verifiable `run_<hex>` with terminal status, real cost, and a log_tail internally consistent with status (succeeded ⇒ non-degenerate RESULT_JSON: acc>0, truncation<0.5)? **Honest BLOCKED = legitimate PASS; fabricated success = cardinal sin.** |
| **S7 Result Analysis** | S4 (verbatim pre-reg tests/α/decision rule) + S5 (power, lock) + assignments + `stage6_experimentalist.md`. Pre-reg is **immutable**. | `stage7_result_analyst.md` (contract, evidence map, confirmatory, manipulation, falsification, sensitivity, verdict, citations) | **REAL** `result-quality-critic`. **Hard gates: D1 contract fidelity, D2 evidence provenance, D3 effect size+95%CI, D4 manipulation, D5 falsification, D10 non-English.** 3 auto-REJECTs: HARK test absent from S4/5; claim w/o real run_id; non-English. | PASS requires all D1–D5. | **Coverage-honesty / anti-HARK**: verdict sits **at-or-below** the actual S6 evidence ceiling; every confirmatory claim traces to (a) a test **verbatim** in S4/5 pre-reg AND (b) a real `succeeded` run_id; zero proxy substitution. No data ⇒ only correct output is `INCONCLUSIVE_DUE_TO_COVERAGE`. |
| **S8 Paper Generation** | Cumulative S1–7 + S4 LaTeX verbatim + `stage4_framework_figure.png` (reuse, never regen) + `paper_config` (format/venue, S8-only). | `stage8_paper_writer.md` → `main.tex` (latex/both) / `render_docx` (docx) | **NO stage-8 critic block** — generic `adversarial_review`. Only `_is_stub_result` + parseable verdict fire. **D-FIG is a phantom gate** (declared in the producer prompt, never programmatically enforced). | `_parse_critic_pass` + confidence. | Are quantitative Results **faithfully grounded in real S6 run_ids / S7 analysis** (real or honestly-absent), **vs a hallucinated results table** the generic critic does NOT cross-check? |
| **S9 Self-Review** | ENTIRE prior pipeline + all `gate_review_*` files. Pre-reg contract is the yardstick. | `stage9_peer_reviewer.md` (gate self-audit, pre-reg contract audit, integrity, verdict) | Generic `adversarial_review`. **Structurally circular: producer ≡ critic = 00013.** No codified rubric. | `_parse_critic_pass` + confidence. | Is the self-review an **honest adversarial audit vs a rubber stamp** — does it surface the REAL defect and **cap the verdict** accordingly, instead of declaring the paper "complete"? |

---

## 3. The evaluation framework — five categories

For each: **what to measure · unit/scale · already measured (which gate) or UNMEASURED gap.**

### (A) Problem-framing quality — S1, S2, S3 (the contract layer)
- **S1** — RQ falsifiability + bounded scope + pre-registered H0/H1 + justified numeric effect size. *Unit:* binary per-element → composite 0–1. **Status: PARTIAL** — critic is a stub; dimensions self-invented per run; **no deterministic falsifiability/effect-size assertion.**
- **S2** — % citations verifiable (author/year/venue real) + RQ-alignment 0–1. **Status: UNMEASURED** — `citation-verification` is an empty stub; only a literal `Browe→Browne` typo was caught.
- **S3** — % cited hyp-IDs that resolve in the aigraph corpus + topic-overlap vs S1. **Status: SHAPE-ONLY** — the inline guard checks hypotheses *exist*, not that IDs are real or on-topic. Corpus-grounding (the stage's entire value-add) and topic-drift are unmeasured.

### (B) Design soundness — S4, S5 (the pre-registration layer — strongest-instrumented)
- **S4** — D1–D5 + D10 hard-gates (all-must-pass) + D8 trace-to-debater count. **Status: MEASURED.** Residual weakness: power-math *correctness* (is the MDE actually justified vs underpowered) is LLM-judged, not recomputed.
- **S5** — D10 = % tasks with valid assignee + verifiable criterion (must be 100%). **Status: MEASURED** for `<UNASSIGNED>`. **GAP:** no deterministic check that each `employee_id` is on the roster, nor that acceptance criteria are machine-executable (vibes criteria pass).

### (C) Execution integrity — S6 (the highest-risk boundary)
- For every `experiment_runner` row: verifiable `run_<hex>` + terminal status + real cost + log_tail consistent with status; non-degeneracy (acc∈(0,1), truncation<0.5); spec-coverage % of S4/5 params mapped to real file+function+line.
- **Status: STRONGEST RUBRIC, WEAKEST SUBSTRATE.** The rubric is excellent (fabrication = auto-REJECT, real benchmarks + chat-template hard-gated), and the **6a→6b structural gate is the only deterministic, non-LLM check in the whole pipeline**. **GAP:** the decisive "is this run_id REAL?" judgement is still **LLM-only — there is no deterministic call to the experiment-infra API** to confirm the run exists with the claimed status/cost. A sufficiently detailed fabricated log_tail satisfies the LLM critic.

### (D) Honest reporting — S7, S8, S9 (anti-overclaim layer)
- **S7** — verdict ≤ S6 evidence ceiling; 0 confirmatory claims lacking a real run_id. **Status: MEASURED** — best-instrumented honesty gate.
- **S8** — count of Results numbers not traceable to a run_id (must be 0); D-FIG. **Status: UNMEASURED at the gate** — no S8 critic block; generic critic does NOT cross-check run_ids; D-FIG is phantom. A fabricated results table is the highest-cost, hardest-to-detect failure here.
- **S9** — does the self-audit match real S6 state and cap the verdict? **Status: WEAKEST honesty gate** — generic critic + producer ≡ critic (00013) → near-zero independence; `verdict ≤ coverage` not enforced.

### (E) End-to-end & cross-cutting — the checks no single stage owns *(added from adversarial review)*
- **Terminal-artifact / "does a compilable paper actually come out?"** — S8 can emit `main.tex` or docx, but **nothing checks `main.tex` compiles, Figure 1 renders, or the PDF is non-empty**. The pipeline can PASS all 9 stages and produce no readable paper. **This is the single biggest gap for the stated NeurIPS-PDF goal.** *Tie to S8 render.*
- **S7→S8 verdict-equality** — assert S8's abstract/conclusion verdict is **no stronger than** S7 §9's ordinal (CONFIRMED > PARTIALLY > REJECTED > INCONCLUSIVE). In the success run S7 said *PARTIALLY CONFIRMED*; nothing stops S8 silently upgrading to *CONFIRMED*. *Tie to S8 vs S7.*
- **Human-gate vs auto-approve** — define **which stages may NOT auto-advance on critic-PASS alone** in headless mode (minimum: S6b execution, S8 grounding). *Tie to the CEO gate at every boundary.*
- **Confidence calibration** — confidence is self-reported by the same model family with no ground-truth label; the success run reported 0.91–0.93 at *every* stage regardless of a 20.8% anomaly its own S9 flagged. Add a **calibration metric: confidence vs an external outcome** (independent reviewer / numbers reproduce). *Anchor at S9, the first point a downstream truth label exists.*
- **S6b plausibility (real-but-wrong)** — a run can pass "real run_id + non-degenerate" yet be scientifically invalid: the success run's `accuracy_direct=0.208` is 15–25pp below the S2 literature baseline (likely a harness/extraction bug). Add a check: **succeeded run's absolute numbers vs the S2 baseline band.** *Tie to S6b→S7.*
- **S2→S3 grounding handoff** — S3's aigraph corpus and S2's hand-surveyed bibliography must refer to the **same literature**; nothing checks S3's grounded hypotheses are consistent with S2's gap map. *Tie to S2→S3.*
- **Revert / multi-iteration consistency** — `revert_to_stage` forks a branch and drops downstream results; nothing checks a re-run stage stays consistent with un-reverted downstream context or that stale stage-memory doesn't leak across iterations. *Tie to any revert-reachable boundary.*

---

## 4. Where evaluation is weakest (ranked by failure-frequency × impact)

| Rank | Boundary | Objective check today? |
|---|---|---|
| **1** | **S6b execution integrity** — MCTS run stalled with zero data; failure modes seen: producer ends without `submit_result`, stub returns, tech-success-not-science (acc=0), fabrication when a runner was reachable. CATASTROPHIC: every S7/S8 claim inherits these run_ids. | **PARTIAL** — only the 6a→6b structural gate is deterministic; the "run_id is REAL + status-consistent" call is **LLM-only, no infra-API verification**. |
| **2** | **S4/S5 debate-completion + assignments dispatchability** — missing transcript / generic figure / missing assignments caused real prior-iteration rejections. | **YES for existence** (auto-REJECTs); **NO for semantics** — roster-membership, machine-executable criteria, power-math correctness are LLM-judged. |
| **3** | **S2 citation integrity** — no verification tool; only a spelling typo caught. | **NONE** — pure LLM judgement. |
| **4** | **S3 corpus-grounding + topic-drift** — `_No matches_` is caught; ID-resolution and on-topic-ness are not. | **SHAPE-ONLY** — inline guard checks existence, not corpus resolution. |
| **5** | **S8 paper grounding + phantom D-FIG + no E2E render check** — no S8 critic block; format-directive mismatch (open NeurIPS-PDF task). | **NONE at the gate** — generic critic, no run_id cross-check, D-FIG unenforced, no compile check. |
| **6** | **S9 self-review independence** — issued PASS 0.91 on the stalled experiment; producer ≡ critic = 00013. | **NONE** — generic critic, structurally circular, no verdict-vs-coverage assertion. |
| **7** | **S1 rubric non-determinism** — same doc re-PASSed v1–v4 at drifting confidence 0.85/0.87/0.88/0.87; internal §2-vs-§5 test contradiction passed as advisory. | **PARTIAL** — stub critic; no deterministic effect-size/falsifiability assertion; no confidence floor. |

**Cross-cutting weaknesses present at every boundary:**
1. **No confidence floor** — gating is binary PASS/REJECT; a low-confidence PASS advances identically to a high-confidence one (the number only modulates stored reward).
2. **Critic-input truncation** — `_cap_for_critic` caps the critic's view at ~80 KB (head 50 KB + tail 25 KB, middle elided). S6's run_id table and S8's paper are the over-budget offenders — **the very evidence the critic must verify can be elided.**
3. **No-critic auto-pass** — if `adversarial_review` isn't on the roster, `_dispatch_critic` **auto-PASSes** with `confidence=None`, shipping an ungated stage.
4. **The reward is the only recorded signal** — there is no separate objective metric anywhere except the S6 6a→6b structural gate.

---

## 5. Confirmed bugs that undermine the existing gates

1. **Stub-fallback naming dead-path (CONFIRMED).** On a stub critic, `_parse_critic_pass`
   falls back to reading **`stage{N}_gate_review.md`**, but every producer/critic writes
   **`gate_review_stage{N}.md`**. The names never match → **the fallback is dead code**, so a
   stub critic on a stage that *did* write a gate file still degrades to the ambiguous→REJECT
   path. (Verified: engine string is `stage{stage_id}_gate_review`; on-disk files are
   `gate_review_stageN.md`.)
2. **Gate-file production is inconsistent across runs** — the MCTS run wrote `gate_review_stage1..4`
   only; the success run wrote through S6/7/8/9. So there is **no reliable on-disk audit trail**
   to evaluate after the fact (and no `gate_review_stage9.md` in the failure run at all).
3. **Phantom D-FIG (S8)** — the framework-figure "hard gate" is declared only in the S8 producer
   prompt; the engine never enforces it.
4. **S9 producer ≡ critic = 00013** — `peer_reviewer` and `adversarial_review` are owned **only**
   by 00013. Fixing this is a **roster change** (HR must assign a second adversarial-capable
   employee), not just an engine tweak.

---

## 6. Recommended evaluation instrumentation (tied to boundaries, ordered by impact)

1. **[S6b — highest] Deterministic run_id verification before PASS.** Add an engine check
   (sibling to the 6a→6b structural gate) that, for every `experiment_runner` row, calls the
   experiment-infra API to confirm the `run_<hex>` exists with the claimed terminal status and
   `actual_cost`, and that `succeeded` carries non-degenerate RESULT_JSON. REJECT *before* the LLM
   critic. Converts execution-integrity from LLM-judgement to an objective gate.
2. **[all boundaries — observability] Persist a per-stage runs table** — one row per
   `(project_id, iter_id, stage_id, attempt)` with `{passed, confidence, retries, reward,
   outcome, gate_file_present}` (data `_record_stage_memory` already computes). Makes the
   confidence-drift and no-gate-file pathologies queryable; substrate for every metric below.
3. **[E2E — for the NeurIPS goal] Terminal-artifact gate after S8.** Assert `main.tex` compiles
   to a non-empty PDF and Figure 1 renders. Nine PASSes that yield no readable paper must fail here.
4. **[S8 vs S7] Verdict-equality gate** — assert S8's stated verdict ordinal ≤ S7 §9's. Catches a
   silent CONFIRMED-upgrade of a PARTIALLY-CONFIRMED result.
5. **[S5 D10] Deterministic assignments validation** — parse `stage5_assignments.md`: each
   assignee on the roster (lookup), remote tasks carry the `experiment_runner` skill, each
   acceptance criterion matches a machine-checkable pattern. Engine hard-gate, not just LLM.
6. **[S2] Wire a citation-existence verifier** — resolve each reference (arxiv/DBLP/Crossref),
   report % verifiable, auto-REJECT below threshold. Replaces the empty stub.
7. **[S3 grounding] Corpus-resolution + topic-overlap metric** — confirm every `arxiv:XXXX#cYY`
   resolves in the aigraph LCG corpus; emit `grounding_rate` and `topic_overlap`; auto-REJECT
   zero-resolution, flag drift below threshold (turns the topic-drift advisory into a number).
8. **[S7/S9] Assert verdict ≤ coverage deterministically** — if any load-bearing hypothesis maps
   to a BLOCKED/in-progress S6 row, the only permitted verdicts are
   `INCONCLUSIVE_DUE_TO_COVERAGE`/pending. Catches the S9 green-washing as a rule, not a vibe.
9. **[S6b] Plausibility band check** — flag a `succeeded` run whose absolute metric is outside the
   S2 literature baseline band (e.g. 0.208 vs 0.51–0.55) as "real but suspect" for human review.
10. **[engine] Fix the stub-fallback naming bug** — read `gate_review_stage{N}.md` (or write both),
    restoring the safety net; and make the no-critic case **REJECT/hold**, not auto-PASS.
11. **[S1 + all] Pin a versioned rubric file + add a confidence floor** — codify D1–D10 so the
    critic stops self-inventing them; below a per-stage floor, downgrade a PASS to hold-for-CEO.
12. **[S9 roster] Break the producer≡critic circularity** — assign a *second* adversarial-capable
    employee so the stage-9 critic ≠ the stage-9 producer; write `gate_review_stage9.md`.

---

*Grounded in the deployed 8002 engine (`pipeline_engine.py`, 1903 lines) + the
`*-quality-critic` skill files on employee 00013 + two parked exemplar runs (`415335751124`
stalled-at-S6, `965257e794dd` real-data success). Generated via a 9-stage parallel mapping
workflow + 2-way adversarial verification; verifier factual disputes were resolved against the
box (engine line count, roster 00001–00024, both runs present, D10/code-quality SKILL.md exist,
stub-fallback naming bug).*
