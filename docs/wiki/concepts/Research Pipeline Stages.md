---
title: Research Pipeline Stages
type: concept
created: 2026-04-24
updated: 2026-04-24
sources: ["[[AutoResearchClaw]]", "[[karpathy-autoresearch]]"]
tags: [pipeline, stages, architecture]
---

# Research Pipeline Stages

The 9 stages of AutoResearch's [[Adversarial Pipeline]], each implemented as an [[OMC Talents|OMC Talent]] pair (producer + critic) communicating via [[OMC Meetings]].

## Stage Map

| # | Stage | Producer Talent | Key Tools | Optional? |
|---|-------|----------------|-----------|-----------|
| 1 | Topic Refinement | topic-refiner | - | No |
| 2 | Literature Survey | literature-surveyor | Semantic Scholar, arXiv, OpenAlex | No |
| 3 | Idea Generation | idea-generator | Semantic Scholar (novelty check) | No |
| 4 | Methodology Design | methodology-designer | - | No |
| 5 | Experiment Design | experiment-designer | - | No |
| 6 | Auto Experiment | experimentalist | Docker sandbox, GPU compute | **Yes** (theoretical papers) |
| 7 | Result Analysis | result-analyst | - | No |
| 8 | Paper Generation | paper-writer | LaTeX (Jinja2 templates) | No |
| 9 | Self-Review | peer-reviewer (x3) | - | No |

## Stage Skipping

Stage 6 (Auto Experiment) is optional. For theoretical papers, the orchestrator routes directly from Stage 5 to Stage 7. Configured via `skip_stages: [experiment_run]` in pipeline YAML.

## Pivot Logic

When Stage 7 results don't support the hypothesis:
- Refine methodology (go to Stage 4)
- Refine hypothesis (go to Stage 3)
- Accept negative result (proceed to paper)

Default: retry same stage 3x, then fall back one stage, max 2 fallbacks.

## Key Insight: Stage 2 Has Highest Failure Rate

Research shows literature review has the highest failure rate among all pipeline stages in automated research systems. AutoResearch invests extra in Stage 2: 4-layer citation verification (Semantic Scholar + arXiv for v1) and contradiction detection.

## Frontend: Breakpoint System

Users can set breakpoints on any stage. Default breakpoints on Stage 3 (Idea Generation) and Stage 9 (Self-Review). At breakpoints, the pipeline pauses and shows a 6-tab action panel: Edit Output, Add Instructions, Override Critic, Skip Stages, Artifacts, Export.
