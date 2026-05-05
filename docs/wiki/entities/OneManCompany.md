---
title: OneManCompany (OMC)
type: entity
created: 2026-04-24
updated: 2026-04-24
sources: ["[[AutoResearchClaw]]"]
tags: [backbone, agent-os, omc]
---

# OneManCompany (OMC)

**GitHub:** github.com/1mancompany/OneManCompany | **Stars:** ~47 | **License:** Apache 2.0

## What It Is

An open-source "agent operating system" that simulates a full AI-powered company. You (human) are the CEO; every other employee (EA, HR, COO, engineers, designers, QA) is an AI agent.

## Core Architecture

- **Vessel** = execution container (retry, timeouts, communication, scheduling)
- **[[OMC Talents|Talent]]** = capability package (skills + tools + personality) that plugs into a Vessel
- **Company** = orchestration layer (CEO delegates to EA/COO, who dispatch to workers)
- **[[OMC Meetings|Meetings]]** = structured multi-agent synchronous discussions

## Tech Stack

Python 3.12, FastAPI, LangChain/LangGraph, Pydantic v2, WebSockets. LLM providers: OpenRouter (default), Anthropic, OpenClaw.

## How AutoResearch Uses OMC

AutoResearch configures OMC as a "Research Lab" company instance:
- Research Director = COO-level Talent orchestrating the [[Research Pipeline Stages|pipeline]]
- Each pipeline stage = one or more [[OMC Talents|Producer Talents]]
- [[Adversarial Pipeline|Adversarial review]] = [[OMC Meetings]] between producer and critic

## Talent Hosting Modes

| Mode | How |
|------|-----|
| Company-hosted | Internal LangChain loop |
| Self-hosted | Claude Code CLI |
| OpenClaw | Subprocess |
| Remote | HTTP polling |

> [!tip] Connection
> OMC gives AutoResearch the agent infrastructure for free: retry logic, task trees, meeting system, talent marketplace. AutoResearch adds domain-specific research talents on top.
