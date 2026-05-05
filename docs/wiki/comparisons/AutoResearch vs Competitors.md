---
title: AutoResearch vs Competitors
type: comparison
created: 2026-04-24
updated: 2026-04-24
sources: ["[[karpathy-autoresearch]]", "[[uditgoenka-autoresearch]]", "[[AutoResearchClaw]]"]
tags: [comparison, competitors, landscape]
---

# AutoResearch vs Competitors

## Comparison Table

| Feature | AutoResearch | [[AutoResearchClaw]] | [[karpathy-autoresearch]] | AI-Scientist (Sakana) | PaperOrchestra (Google) |
|---------|-------------|---------------------|--------------------------|----------------------|------------------------|
| **Scope** | Full pipeline | Full pipeline | Training loop only | Full pipeline | Paper writing only |
| **Stages** | 9 | 23 | 1 (loop) | ~8 | Decoupled |
| **Core approach** | [[Adversarial Pipeline]] | Multi-agent debate | Single agent loop | End-to-end | Writing from logs |
| **Quality mechanism** | Critic at every stage | Human-in-loop co-pilot | Val loss comparison | Self peer review | Citation density |
| **Backbone** | [[OneManCompany]] | Custom orchestrator | Shell scripts | Custom | Custom |
| **Model dependency** | Agnostic (via OMC) | OpenAI primary | Agent-agnostic | Multiple | GPT-4o |
| **Maturity** | Design phase | v0.4 (5 weeks) | v1 (1 month) | Nature published | April 2026 |
| **Stars** | - | ~11.6K | ~76K | ~5K | New |

## Key Differentiators

### vs AutoResearchClaw
AutoResearchClaw has more stages (23 vs 9) but no adversarial gate at every stage. AutoResearch trades stage count for stage rigor. AutoResearchClaw is OpenAI-dependent; AutoResearch is model-agnostic via OMC.

### vs karpathy/autoresearch
Karpathy's version is beautifully simple but narrow (single-file training optimization). AutoResearch extends the concept to the full research pipeline while keeping the core insight (iterate and verify).

### vs AI-Scientist
AI-Scientist pioneered the concept and is published in Nature. But output quality is described as "mediocre" and "template-like." AutoResearch addresses this directly with [[Calibrated Confidence]] and adversarial self-review.

### vs PaperOrchestra
Google's approach deliberately decouples paper writing from experiment execution. AutoResearch integrates both but makes Stage 6 (experiments) optional for theoretical papers.

> [!info] Source
> This comparison is based on research conducted during the /office-hours session on 2026-04-23, including direct analysis of all three GitHub repos and web search for AI-Scientist and PaperOrchestra.
