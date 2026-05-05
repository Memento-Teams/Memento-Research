---
title: OMC Talents
type: concept
created: 2026-04-24
updated: 2026-04-24
sources: []
tags: [omc, architecture, modularity]
---

# OMC Talents

A **Talent** is the capability/personality package in [[OneManCompany|OMC]]. Think of it as a mech pilot: the Talent (pilot) plugs into a Vessel (mech), and together they become an Employee.

## Talent Structure

```
talents/{talent-id}/
├── profile.yaml       # Identity, role, personality, hosting mode
├── skills/            # Markdown files injected into prompts
├── tools/             # LangChain @tool implementations + manifest
├── manifest.json      # Settings UI, capabilities (optional)
└── launch.sh          # For self-hosted modes (optional)
```

## AutoResearch Talents

| Talent | Role | Stage |
|--------|------|-------|
| topic-refiner | Producer | 1 |
| literature-surveyor | Producer | 2 |
| idea-generator | Producer | 3 |
| methodology-designer | Producer | 4 |
| experiment-designer | Producer | 5 |
| experimentalist | Producer | 6 |
| result-analyst | Producer | 7 |
| paper-writer | Producer | 8 |
| adversarial-critic | Critic (shared) | All |
| peer-reviewer (x3) | Reviewer | 9 |
| research-director | Orchestrator | - |

## Key Design Decision

One shared `adversarial-critic` Talent with multiple skills (novelty assessment, methodology critique, experiment validation) rather than 9 separate critic Talents. The Research Director assigns the appropriate skill context when dispatching to each [[OMC Meetings|meeting]].

## Talent Market

Research Talents can be published to OMC's Talent Market for community distribution. This enables domain-specific contributions (e.g., bioinformatics experimentalist, NLP benchmark expert).
