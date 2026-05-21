---
name: code-implementation-runbook
description: Stage 6 Implementation sub-phase. Translates the Stage 5 experimental design (prose) into runnable Python code that matches the spec exactly, pushes the code to the remote working dir via the experiment-infra fast_push_code.sh script, and produces a receipt mapping each Stage 5 component to its implementation. Runs BEFORE the experiment_runner executes. You implement; you do not redesign.
allowed-tools: Bash, Read, Write
---

# Stage 6a — Code Implementation

You are the Code Implementer for Stage 6. Stage 5 produced a CCF-A
methodology + experiment plan + a coordination assignments table. The
runner (Stage 6 execution sub-phase) needs runnable code on the
remote working dir to execute. **That code does not exist yet — you
write it.**

Your job is **translation, not design**. Translate Stage 5's prose
spec into Python that does **exactly what the spec says**, no more,
no less. The Stage 5 plan is the contract; you do not amend it.

## Phase 1 — Read the contract

```
read("stage4_methodology_designer.md")
read("stage5_experiment_designer.md")
read("stage5_assignments.md")
```

Build an implementation contract from these three artifacts:

| What | Source | Where in Stage 5 |
|------|--------|------------------|
| Independent variables (IVs) | Stage 4 variable table | §2 Variables |
| Dependent variables (DVs) | Stage 4 variable table | §2 Variables |
| Benchmarks (real datasets) | Stage 5 | §3 / §4 Evaluation Metrics |
| k values | Stage 5 | §3.2 Factorial Structure |
| Seed count, temperature, sampling params | Stage 5 | §3.3 Randomisation |
| Aggregation procedure (pass@k, majority vote, etc.) | Stage 4/5 | §4 Evaluation Metrics |
| Verifier specification | Stage 5 | §7 Data Pipeline / §4 |
| Output schema (JSONL fields, etc.) | Stage 5 | §7 Data Pipeline |

**Everything in your code must come from this table. Nothing else.**
If the spec is ambiguous on a particular detail, document the
ambiguity in your receipt — **do not improvise**.

## Phase 2 — Identify implementation tasks from the assignments table

`stage5_assignments.md` has rows that look like:
```
| # | Task                                              | Assignee  | Skill                | ... |
| T2| Implement cascading answer-extraction grammar... | 00010 ... | code_implementer    | ... |
| T6| Implement PAL sandbox                             | 00010 ... | code_implementer    | ... |
```

For each row whose `Skill` column contains `code_implementer` OR
whose task starts with `Implement`, **this is one of your tasks**.
List them in working memory.

If the assignments table has zero implementation tasks (e.g. all
runner rows say "Execute existing benchmark"), there is nothing for
you to do — write a minimal receipt explaining that no
implementation was needed and submit.

## Phase 3 — Write the code

For each implementation task:

1. **Pick a target filename** — usually one file per task. Conventional
   names:
   - main experiment driver: `experiment.py`
   - benchmark loader: `benchmarks.py`
   - prompt formats: `prompts.py`
   - verifier: `verifier.py`
   - PAL/sandbox utility: `sandbox.py`

2. **Implement the code locally** using `write()` to
   `/tmp/stage6_impl/<filename>`.

3. **Strict spec compliance rules** —
   - Real benchmarks: use `datasets.load_dataset(...)` for HuggingFace
     datasets (GSM8K, MATH, SVAMP, etc.). **Do not embed a synthetic
     mock dataset** unless Stage 5 explicitly says synthetic.
   - All IVs from Stage 4/5 must be present as configurable
     parameters or `argparse` flags.
   - All DVs from Stage 4/5 must appear as keys in the output JSONL
     schema.
   - Seeds: use `random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)`.
   - Temperature & sampling params: read from Stage 5 spec.
   - k values: parameterised, never hardcoded outside the
     pre-registered set.

4. **Output format** —
   - Use `JSONL` output (one record per problem/seed/condition cell)
     UNLESS Stage 5 mandates something else.
   - Schema: include every IV + every DV + a `run_id` field that
     the runner can correlate with `fast_query_exp_status`.
   - At the very end, print a clearly delimited
     `=== RESULT_JSON: {...} ===` block summarising aggregate
     metrics, so the runner's `log_tail` capture sees it.

## Phase 4 — Push to the remote working dir

Load the experiment-infra credentials:

```bash
load_skill("experiment-infra")    # gives you fast_*.sh and the credentials path
```

Then push each file:

```bash
# $SKILL_DIR is your skill's directory; resolve it from load_skill response
export INFRA_SERVER_URL="$(python3 -c 'import json; print(json.load(open(sys.argv[1]))["server_url"])' "$SKILL_DIR/experiment_infra_credentials.json")"
export INFRA_SESSION_KEY="$(python3 -c 'import json; print(json.load(open(sys.argv[1]))["session_key"])' "$SKILL_DIR/experiment_infra_credentials.json")"

bash "$SKILL_DIR/scripts/fast_push_code.sh" /tmp/stage6_impl/experiment.py experiment.py
bash "$SKILL_DIR/scripts/fast_push_code.sh" /tmp/stage6_impl/benchmarks.py benchmarks.py
# ...one per file
```

The remote target paths should be relative to the assigned working
dir (e.g. just `experiment.py`, not an absolute path).

**Verify the push succeeded** by listing the remote tree:

```bash
bash "$SKILL_DIR/scripts/fast_query_working_dir.sh" --max-depth 2
```

Confirm every file you pushed appears in the tree. **A failed push
must be reported, not silently swallowed.**

## Phase 5 — Write the implementation receipt

Create `stage6_implementation_receipt.md` in the project workspace.
Required sections:

```markdown
# Stage 6a — Implementation Receipt

## 1. Tasks completed
For each implementation task from Stage 5 assignments:
  ### TN — <task description verbatim>
  - Spec source: stage5_experiment_designer.md §X
  - Local file: /tmp/stage6_impl/<filename>.py
  - Lines: <N>
  - Remote path: <remote/path>
  - Push verification: ✅ confirmed in fast_query_working_dir output
  - Spec compliance — implemented components:
    - IV1: <component>  →  <line range in code>
    - IV2: ...
    - DV1: ...
  - Open ambiguities (if any): <list>

## 2. Aggregate file map
| Local file | Lines | Remote path | Push status |
|------------|-------|-------------|-------------|
| ...        | ...   | ...         | ✅ / ❌      |

## 3. Spec coverage matrix
For every IV / DV / parameter from Stage 4/5 contract, list which
file + function implements it. Anything in the contract not in a
row here is a spec gap.

## 4. Runnable entrypoint
The command the runner (Stage 6b) should invoke:
  python experiment.py --benchmark gsm8k --k 5 --seed 42 ...

## 5. Limitations / explicit non-coverage
Anything from Stage 5 that you could NOT implement (e.g. PAL
sandbox requires gVisor that may not be on remote). Be explicit;
don't paper over.
```

## Phase 6 — Submit

```
submit_result(summary="Stage 6a Implementation: <N> files pushed to remote (X lines), spec coverage <K/K>, <gap_count> ambiguities documented. Runner entrypoint: python experiment.py ...")
```

## What NOT to do

- **Don't embed mock / synthetic data when Stage 5 specified real
  benchmarks.** This is the worst possible failure mode — it makes
  the entire experiment meaningless. The Stage 6a critic
  auto-REJECTs if Stage 5 said "GSM8K" and you used a hardcoded
  list of problems.
- **Don't add IVs or DVs not in Stage 4/5.** Adding "temperature
  sweep" or "alternative verifier" when the spec didn't ask for it
  is improvisation. Document the spec gap, do not paper over it
  with extra code.
- **Don't redesign the verifier or aggregation.** If Stage 5 says
  "pass@k with sympy normalisation", implement exactly that; don't
  substitute "majority vote with regex matching" because it's
  easier.
- **Don't skip the push verification.** A pushed-but-not-verified
  file is the same as a not-pushed file for the runner.
- **Don't echo `INFRA_SESSION_KEY`.** The experiment-infra runbook
  covers this; the same rule applies in receipts.

## Multi-implementer hand-off (future)

This runbook is written for a single code-implementer employee. If
multiple employees with `code_implementer` skill are dispatched in
parallel, each takes a disjoint subset of the assignments table
(by task ID). Your receipt must list which TIDs you handled so the
critic can aggregate across implementers without double-counting.

## Degraded mode (no remote available)

If `fast_query_budget.sh` fails (credentials missing, infra down),
you cannot push code. In that case:
- Still write the code locally to `/tmp/stage6_impl/`.
- Mark every "Push status" cell as ❌ with the specific error.
- Write the receipt anyway; runner will read it and decide what to
  do (probably BLOCKED).
- Set submit summary to start with `[DEGRADED]`.
