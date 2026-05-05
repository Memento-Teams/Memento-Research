---
title: AutoResearchClaw
type: source
created: 2026-04-24
updated: 2026-04-24
sources: []
tags: [competitor, full-pipeline, aiming-lab]
---

# AutoResearchClaw (aiming-lab)

**GitHub:** github.com/aiming-lab/AutoResearchClaw | **Stars:** ~11.6K | **Created:** March 2026

## Summary

Fully autonomous research pipeline: "Chat an Idea. Get a Paper." 23-stage pipeline covering topic refinement, literature search, hypothesis generation via multi-agent debate, experiment design, code generation, sandboxed execution, result analysis, paper drafting, LaTeX compilation, citation verification, and peer review.

## Tech Stack

Python 3.11+, OpenAI (primary), Anthropic/Gemini adapters, Docker for experiments, MCP protocol, OpenClaw integration.

## Key Features

- 23-stage pipeline orchestrator
- Anti-hallucination: VerifiedRegistry, claim verification, citation pruning
- Self-healing experiments with diagnosis/repair loop
- 10+ domain adapters (ML, physics, biology, chemistry, economics)
- Human-in-the-Loop co-pilot with 6 granularity levels
- Cross-run learning via MetaClaw

## Strengths

Most comprehensive existing solution. 2,699 tests passing. 8 showcase papers.

## Weaknesses

- Heavily OpenAI-dependent
- 5 weeks old, rapidly iterating (unstable API surface)
- Generated papers labeled as drafts requiring human review
- Complex configuration surface

## Relevance to AutoResearch

The most direct competitor. AutoResearch differentiates with:
1. **[[Adversarial Pipeline]]** (skepticism over throughput)
2. **[[OneManCompany|OMC backbone]]** (vs custom orchestrator)
3. **[[Calibrated Confidence]]** (vs binary pass/fail)
4. **Model-agnostic** (vs OpenAI-dependent)

> [!question] Open question
> Should AutoResearch fork AutoResearchClaw or build from scratch? Decision was made to build on [[OneManCompany|OMC]] from scratch, using AutoResearchClaw as reference only.
