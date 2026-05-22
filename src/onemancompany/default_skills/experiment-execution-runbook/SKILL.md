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

## Order of operations — DO ALL FOUR, IN ORDER

You have a bounded agent budget. Burning it on polling/queries and never
writing the report = WORST outcome (Stage 6b critic will REJECT, the
whole run wastes the GPU time you already spent). Treat this list as
a strict checklist:

1. **Phase 1** — read `stage5_assignments.md` (1 call).
2. **Phase 2** — for each runner row, submit + poll-to-terminal. **Cap
   polling at 10 status calls per run_id**; if not terminal, capture
   what you have and move on.
3. **Phase 3** — write `stage6_experimentalist.md`. **This is
   MANDATORY** — do it even if Phase 2 hit issues. Empty/14-char
   `submit_result` summaries are an auto-REJECT.
4. **Phase 4** — `submit_result(...)` with run_ids and a real summary
   string (>= 100 chars).

If you sense you're approaching agent loop limits (you've made many bash
calls and feel pressure to do more), **stop submitting/polling
immediately** and jump to Phase 3 to write the report with whatever
evidence you have so far. A partial report >> no report.

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

For each `experiment_runner` row, do these in order and **track your
budget** — every step is a bash call against your agent iteration limit:

1. **One-shot setup** (do once, before any rows): run
   `fast_query_budget.sh` for liveness; `read("stage6_implementation_receipt.md")`
   to get the entrypoint Stage 6a chose; extract `<project_id>` and
   `<iter_id>` from your workspace path (`.../projects/<project_id>/iterations/<iter>`).
   - If `fast_query_budget.sh` errors with missing env vars: STOP —
     report `blocked: INFRA_SERVER_URL / INFRA_SESSION_KEY not set` and
     skip to Phase 3.
   - If `stage6_implementation_receipt.md` is missing: STOP — report
     `blocked: Stage 6a impl not complete, no implementation receipt on disk`
     and skip to Phase 3.
   - Your remote code lives at `omc/<project_id>/<iter_id>/`, NOT at
     the flat working dir root. **Skip** `fast_query_working_dir.sh`
     and `fast_query_server_info.sh` unless the receipt is missing
     critical info — they cost calls and rarely add signal.

2. **Submit.** Use `fast_submit.sh` with a command that `cd`s into the
   per-project subdir first:
   - `-c "cd omc/<project_id>/<iter_id>/ && python experiment.py <args>"`
     for the typical case where Stage 6a's `experiment.py` is the
     entrypoint listed in the receipt.
   - `--yaml <path>` only when the Task explicitly names a YAML in
     `default_skills/experiment-infra/assets/`.
   - `--config` defaults to `base.conf.json` (run_local:true). Use
     `skypilot_container.conf.json` only when the Task explicitly asks
     for SkyPilot.

3. **Record the run_id immediately** in your working notes — every
   subsequent call needs it.

4. **Poll status — capped at 10 calls per run_id.** Use
   `fast_query_exp_status.sh <RUN_ID> --summary`. Stop polling when
   status is terminal (`succeeded` / `failed` / `rejected`) **OR** when
   you've made 10 polls, whichever comes first. If still running after
   10 polls, capture the partial summary and move on to Phase 3 — the
   experiment will keep running on remote; the runner critic accepts
   "still running after N polls, partial evidence below" if you say so
   explicitly.
   - Cadence: ~30s between polls for short runs, 2-5 min for long
     training jobs. Do NOT bash-sleep between polls (wastes iteration
     budget); just queue the next call.

5. **Capture evidence on terminal status.** One final
   `fast_query_exp_status.sh <RUN_ID>` (no `--summary`) to grab the
   full `log_tail` (capped at ~32KB), `metrics`, `actual_cost`,
   `started_at`, `finished_at`. This is the evidence you'll paste into
   Phase 3.

### Path B: Skill is a non-runner skill (e.g. `causal-inference`, `paper_writer`)

Stage 6's job is the **execution layer**, not the analysis or writing
layer. For each non-runner row:

- Note it as **deferred** in the report.
- Cite the named assignee and skill.
- Do not attempt to run it yourself.

### Path C: Assignee is `<UNASSIGNED>` or skill is empty

Flag explicitly in the report as a Stage 5 gap. Do not silently skip.

## Phase 3 — Consolidate into `stage6_experimentalist.md` (MANDATORY)

**This phase is non-skippable.** Even if Phase 2 ran into errors,
credentials issues, or polling caps — you MUST write
`stage6_experimentalist.md` before calling `submit_result`. Reports
shorter than ~1 KB or missing the skeleton sections below will trip
the Stage 6 critic.

Write a single structured report using `write()`. Skeleton:

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

Include run_ids in the summary so the critic can spot-check them. The
summary string **must be at least 100 characters** and contain at
minimum: count of runs, status breakdown, total cost, run_ids list,
and a pointer to `stage6_experimentalist.md`. A single-word summary
like `"Executed: bash"` is the historical failure mode and an
auto-REJECT signal — the critic will treat it as no work done.

**Order is fixed**: write the report file FIRST, then submit_result.
Do not call submit_result before the .md file is on disk.

## What NOT to do

- **Don't stop after `fast_submit.sh` returns a run_id.** Submitting
  is step 2 of 5; the experiment is not "done" until you (a) polled to
  terminal or hit the 10-poll cap, (b) wrote
  `stage6_experimentalist.md`, and (c) called `submit_result` with a
  real summary string. Stopping early leaves the critic with no
  evidence — historical auto-REJECT.
- **Don't infinite-poll.** Hard cap is 10 `fast_query_exp_status.sh`
  calls per run_id. After that, write down what you have and finish
  the phase. Burning iterations on polling and never writing the
  report = worst possible outcome.
- **Don't submit `submit_result` with a stub string** like
  `"Executed: bash"` or `"Done."`. The historical failure mode is a
  14-character summary with no run_ids, no costs, no pointer to the
  report file — the Stage 6 critic treats this as no work performed.
  Summary must be ≥100 chars and reference the .md file.
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
