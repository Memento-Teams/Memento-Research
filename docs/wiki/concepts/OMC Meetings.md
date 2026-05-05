---
title: OMC Meetings
type: concept
created: 2026-04-24
updated: 2026-04-24
sources: []
tags: [omc, adversarial-review, collaboration]
---

# OMC Meetings

The mechanism by which [[Adversarial Pipeline|adversarial review]] happens in AutoResearch. An OMC Meeting is a synchronous multi-agent discussion with structured minutes.

## How Meetings Work in AutoResearch

1. Research Director schedules a Meeting between a Producer [[OMC Talents|Talent]] and the Adversarial Critic Talent
2. Producer presents output
3. Critic reviews, challenges, and scores
4. Meeting produces structured minutes (= rejection trace if rejected)
5. Research Director reads the minutes and decides: PASS, RETRY, or PIVOT

## Frontend Representation

Each pipeline stage is visualized as a Meeting Card:
- Header: "Meeting: [Producer] + Adversarial Critic"
- Two participant avatars with "vs" divider
- Split columns: Producer output (left) | Critic review (right, terracotta background)
- Confidence gauge at bottom
- Badge: In Progress / Passed / Rejected

## Meeting States

| State | Visual |
|-------|--------|
| In Progress | Gold border, animated badge |
| Concluded (Pass) | Green-subtle badge |
| Rejected | Coral border, rejection trace below |
| Paused (breakpoint) | Gold border + glow, action panel appears |

> [!tip] Connection
> This is architecturally elegant: [[OneManCompany|OMC]]'s existing meeting system provides the debate mechanism for adversarial review for free. No custom code needed for the debate protocol itself.
