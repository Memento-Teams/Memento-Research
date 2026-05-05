---
title: Adversarial Pipeline
type: concept
created: 2026-04-24
updated: 2026-04-24
sources: ["[[AutoResearchClaw]]", "[[karpathy-autoresearch]]"]
tags: [core-concept, architecture, adversarial]
---

# Adversarial Pipeline

The core architectural differentiator of AutoResearch. Every pipeline stage has two agents: a **producer** that generates output and a **critic** (red-team agent) that tries to kill the output before it advances.

## Why

LLMs are massively overconfident. Research from 2025 shows:
- 49.71% accuracy with 39.25% Expected Calibration Error
- AI writing quality DEGRADES with complexity (inverse of humans)
- Literature review has the highest failure rate among pipeline stages

Optimizing for **completion** (like competitors do) produces mediocre papers. Optimizing for **rigor** produces better science.

## How It Works

```
Producer Agent → Output → Critic/Red-Team Agent
                              ↓
                         PASS → next stage
                         FAIL → loop back (max N retries, then PIVOT)
```

Each gate decision produces:
- `pass: bool`
- `confidence: float (0-1)`
- `issues: list[str]`

Gate logic: pass requires `pass=True AND confidence >= 0.6` (configurable per stage).

## Implementation via OMC

In the [[OneManCompany|OMC]] architecture, adversarial review happens as an [[OMC Meetings|OMC Meeting]]:
- Producer Talent and Critic Talent are pulled into a synchronous meeting
- Meeting minutes become the rejection trace
- The Research Director handles pivot decisions based on meeting outcomes

## Pivot Policy

Default: retry same stage up to 3 times, then fall back one stage (e.g., Stage 7 fails -> retry Stage 4), max 2 fallbacks before terminating with partial output.

## Second Opinion Origin

This concept was validated by an independent Claude subagent cold read during the office hours session. The subagent independently proposed "adversarial self-review with calibrated confidence gates" as the coolest version of this system, confirming it as the core differentiator.

> [!tip] Connection
> This is the opposite of [[AutoResearchClaw]]'s approach, which optimizes for throughput (23 stages, run them all, get a paper). AutoResearch says: fewer stages, but each one must survive scrutiny.
