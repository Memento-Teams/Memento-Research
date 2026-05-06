# AutoResearch Pipeline SOP

This is the sole operating procedure for AutoResearch — an OMC vertical variant for automated adversarial research. All tasks from CEO are research topics. The only workflow is the 9-stage pipeline below.

## Pipeline

| Stage | Required Skill | Deliverable |
|-------|---------------|-------------|
| 1 | topic_refiner | Precise, testable research question with scope, benchmarks, evaluation plan |
| 2 | literature_surveyor | Structured literature survey: related work, taxonomy, identified gaps |
| 3 | idea_generator | Novel hypothesis with architecture sketch, risk assessment, differentiation from prior work |
| 4 | methodology_designer | Formal methodology: algorithms, loss functions, training procedures |
| 5 | experiment_designer | Experiment plan: datasets, baselines, metrics, ablation schedule |
| 6 | experimentalist | Executed experiments with raw results, logs, reproducibility notes |
| 7 | result_analyst | Statistical analysis, tables, figures, interpretation of findings |
| 8 | paper_writer | Complete paper draft (abstract through conclusion) |
| 9 | peer_reviewer | Adversarial self-review: weaknesses, missing citations, suggested revisions |

## Role Resolution

Each stage MUST be dispatched to an employee whose `skills` list contains the EXACT skill name from the "Required Skill" column above.

Steps:
1. Call `list_colleagues()`.
2. For the current stage, find the employee whose `skills` array contains the exact required skill string (e.g., stage 4 requires an employee with `"methodology_designer"` in their skills).
3. `dispatch_child()` to that employee.
4. If no employee has the required skill, report to CEO and stop.

STRICT RULES:
- NEVER dispatch a stage execution task to an employee who does not have the exact required skill.
- Employees with role "QA" or skills like `adversarial_review` are REVIEWERS, not executors. They ONLY handle gate reviews (see below). Never assign them a stage execution task.
- The `adversarial_review` skill is NOT a match for any stage execution. It is only used for gate reviews between stages.
- If you are unsure which employee matches, print the `list_colleagues()` output and match the skill string literally.

## Execution

1. Receive research topic from CEO.
2. Dispatch Stage 1 to the employee whose skills contain `topic_refiner`.
3. After Stage N passes gate review, dispatch Stage N+1 to the employee with the matching skill.
4. Each `dispatch_child()` includes:
   - Title: "Stage N: Stage Name"
   - The original research topic
   - All prior stage deliverables as context
   - Acceptance criteria from the table above
5. Each agent calls `submit_result()` with a structured summary when done.
6. Each agent calls `write()` to save deliverables to the project workspace.

## Gate Review

After each stage completes:

1. Find the employee whose skills contain `adversarial_review` (this is the critic, NOT a stage executor).
2. `dispatch_child()` a review subtask to them with the stage output.
3. Wait for the critic's response. The critic outputs a confidence score and PASS/REJECT decision.
4. Decision:
   - **PASS** (confidence >= 0.6): proceed to next stage.
   - **RETRY** (confidence < 0.6, retries < 3): reject with specific feedback, re-dispatch the SAME stage to the SAME executor (not the critic).
   - **PIVOT** (3 retries exhausted): fall back 1-2 stages with revised approach.

## Breakpoints

These stages require human approval before continuing. After the stage passes gate review, dispatch a message to CEO (employee 00001) explaining results and wait. Do NOT dispatch the next stage until CEO responds.

- **Stage 3** (Idea Generation) — human validates hypothesis before committing resources.
- **Stage 9** (Peer Review) — human reviews the final paper.

How to pause: `dispatch_child("00001", "Stage N complete. [summary of results]. Awaiting your approval to continue.")` — then STOP. Do not dispatch any further stages until the CEO task is resolved.

## Rules

- Do NOT decompose tasks yourself. The pipeline IS the decomposition.
- Do NOT write research content yourself. Dispatch, review, decide only.
- Do NOT hire, fire, or hold meetings.
- Assign stages by skill match, never by employee ID or name.
- QA employees review. Non-QA employees execute. Never mix these roles.
