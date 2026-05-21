---
name: code_implementer
description: Marker skill for the Experiment Code Writer talent. Real workflow lives in the `code-implementation-runbook` runbook, which the platform auto-injects on hire.
---

# code_implementer — marker skill

Carrying this skill tells the platform two things:

1. This employee should auto-receive the `code-implementation-runbook` and
   `experiment-infra` runbooks on hire (handled by
   `_SKILL_REQUIRED_RUNBOOKS["code_implementer"]` in
   `src/onemancompany/agents/onboarding.py`).
2. Stage 6 dispatch logic routes the Stage 6a (Implementation) sub-phase to
   whoever carries `code_implementer`, confident that they hold both the
   implementation runbook (translation discipline) and the experiment-infra
   runbook (so they can push code with `fast_push_code.sh`).

## How to actually implement experiments

Use the `code-implementation-runbook` runbook for the workflow:

```text
load_skill("code-implementation-runbook")
```

That returns the step-by-step instructions for translating the Stage 5
prose plan into runnable Python, validating it against the spec, and
shipping it via experiment-infra.

## Hard rules (auto-REJECT triggers in code-quality-critic)

- **No mock data**: if the spec says a real benchmark, load the real
  benchmark. Hand-typed sample lists are auto-REJECT.
- **No new variables**: never introduce IVs or DVs not in Stage 4/5. Spec
  ambiguities go in the receipt, not the code.
- **English only**: code, comments, commit messages, and receipts must be
  English.
