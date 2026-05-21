---
name: experiment-execution-runbook
description: Stage 6 (Auto Experiment) runbook. Reads stage5_assignments.md row by row and dispatches each task — using the experiment-infra API for remote-execution rows and explicitly deferring non-runner rows. Writes a consolidated, evidence-bearing report to stage6_experimentalist.md.
allowed-tools: Bash, Read, Write
---

# Stage 6 — Auto Experiment Executor

You are dispatching Stage 6 (Auto Experiment). Stage 5 finished and the
debate-convener wrote two artifacts you must consume:

- `stage5_experiment_designer.md` — methodology + experiment plan
- `stage5_assignments.md` — coordination table (the dispatch input)

Your job is **execution, not authoring**. Do not redesign the experiment.
Do not invent missing details. If the plan is unclear or missing required
inputs (commands, working dirs, success metrics), record this as a
blocking issue and STOP — do not improvise.

## Phase 1 — Read the assignments table

```
read("stage5_assignments.md")
```

The table columns are: `# | Task | Assignee | Skill | Due | Acceptance criterion`.
Walk each row top-to-bottom and route by the **Skill** column.

## Phase 2 — Route each row

### Path A: Skill includes `experiment_runner` (remote execution)

You have the experiment-infra runbook on hand. Load it once at the top of
Phase 2:

```
load_skill("experiment-infra")
```

For each `experiment_runner` row:

1. **Confirm credentials.** Run `fast_query_budget.sh` first as a liveness
   check. If it errors with missing env vars, STOP — the report must say
   "blocked: INFRA_SERVER_URL / INFRA_SESSION_KEY not set". Do not invent
   credentials.
2. **Read `stage6_implementation_receipt.md` first.** Stage 6a Code
   Implementer should have pushed code under
   `omc/<project_id>/<iter_id>/` and written this receipt listing the
   exact files + runnable entrypoint. If the receipt is missing,
   Stage 6a didn't finish — STOP with "blocked: Stage 6a impl not
   complete, no implementation receipt on disk" and do not improvise.
3. **Locate the per-project remote subdir.** Extract `<project_id>`
   from your workspace path (`.../projects/<project_id>/iterations/<iter>`).
   Your remote code lives at `omc/<project_id>/<iter>/`, NOT at the flat
   working dir root. Do NOT use any pre-existing code at the flat root
   (e.g. someone else's `stage6_experiment/`) — that is not yours and
   would mix data across projects.
4. **(Optional) survey remote state.** `fast_query_working_dir.sh` to
   confirm Stage 6a's files are in `omc/<project_id>/<iter>/`;
   `fast_query_server_info.sh` for conda/HF cache roots.
6. **Submit.** Use `fast_submit.sh` with a command that `cd`s into the
   per-project subdir first:
   - `-c "cd omc/<project_id>/<iter>/ && python experiment.py <args>"`
     for the typical case where Stage 6a's `experiment.py` is the
     entrypoint listed in the receipt.
   - `--yaml <path>` only when the Task explicitly names a YAML in
     `default_skills/experiment-infra/assets/`.
   - `--config` defaults to `base.conf.json` (run_local:true). Use
     `skypilot_container.conf.json` only when the Task explicitly asks
     for SkyPilot.
7. **Record the run_id immediately** — every subsequent call needs it.
8. **Poll status** with `fast_query_exp_status.sh <RUN_ID> --summary` at
   the cadence the task suggests (1–5 min for short runs, longer for
   training jobs). Stop polling when status is terminal:
   `succeeded` / `failed` / `rejected`.
9. **Capture evidence.** When terminal, drop `--summary` to grab the
   full `log_tail` (capped at ~32KB). Pull the final `metrics`,
   `actual_cost`, `started_at`, `finished_at`.

### Path B: Skill is a non-runner skill (e.g. `causal-inference`, `paper_writer`)

Stage 6's job is the **execution layer**, not the analysis or writing
layer. For each non-runner row:

- Note it as **deferred** in the report.
- Cite the named assignee and skill.
- Do not attempt to run it yourself.

### Path C: Assignee is `<UNASSIGNED>` or skill is empty

Flag explicitly in the report as a Stage 5 gap. Do not silently skip.

## Phase 3 — Consolidate into `stage6_experimentalist.md`

Write a single structured report. Skeleton:

```markdown
# Stage 6 — Auto Experiment Results

## Tasks executed (path A — remote runner)

### T1 — <verbatim task description from assignments table>
- assignee skill: experiment_runner
- run_id: run_xxxxxxxx
- submitted_at: 2026-XX-XXTXX:XX:XX
- finished_at: 2026-XX-XXTXX:XX:XX
- status: succeeded | failed | rejected
- estimated_cost: $X.XX
- actual_cost: $X.XX
- key metrics: {...}
- log_tail excerpt (last 30 lines or relevant signal):
  ```
  <paste from fast_query_exp_status response>
  ```

## Tasks deferred (path B — non-runner skills)

| # | Task | Assignee | Skill | Reason |
|---|------|----------|-------|--------|
| T4 | Statistical analysis | 00101 Priya | causal-inference | Not in Stage 6 scope; awaiting Stage 7 |

## Gaps flagged (path C)

| # | Task | Issue |
|---|------|-------|
| Tn | ... | Assignee was `<UNASSIGNED>` in Stage 5 |

## Aggregate summary

- tasks executed: <N>
- tasks deferred: <M>
- tasks blocked: <K>
- total actual cost: $<X.XX>
- overall verdict: ALL_SUCCEEDED | PARTIAL | BLOCKED
```

## Phase 4 — Submit

```
submit_result(summary="Stage 6: <N> remote runs (<succ/fail>), <M> deferred, total $<X.XX>. See stage6_experimentalist.md and run_ids: [...].")
```

Include run_ids in the summary so the critic can spot-check them.

## What NOT to do

- **Don't fabricate run_ids or metrics.** If a submit failed, report
  `status: failed` and paste the error. Made-up results are an
  auto-REJECT from the Stage 6 critic.
- **Don't simulate when a runner is available.** If `experiment_runner`
  is the assigned skill and the experiment-infra API is reachable, you must
  actually submit — not describe what would happen.
- **Don't run experiments locally on the OMC host.** Remote execution
  goes through experiment-infra. Local-only work is deferred to its assignee.
- **Don't echo `INFRA_SESSION_KEY`.** The experiment-infra runbook covers
  this; the same rule applies in the consolidated report.
- **Don't re-design the experiment.** The Stage 5 plan is the source of
  truth. If it's wrong, file a blocking issue and STOP — do not patch it
  in your report.

## Degraded mode (no `experiment_runner` employee on roster)

If you reach Phase 2A but realize you (the dispatcher) don't have the
experiment-infra runbook (the platform routed this Stage 6 to an employee
without `experiment_runner` skill — typically a fallback `experimentalist`):

- Mark every Path A row as **blocked — no runner skill available**.
- Do not simulate.
- Submit a report that surfaces the gap so the CEO can hire an
  experiment_runner and re-run Stage 6.
