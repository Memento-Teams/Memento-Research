---
title: Overview
type: synthesis
created: 2026-04-24
updated: 2026-04-24
sources: []
tags: [overview, architecture, vision]
---

# AutoResearch Overview

AutoResearch is an **adversarial research pipeline** that automates end-to-end scientific paper generation with a focus on **science quality over throughput**. Unlike competitors that optimize for completion, AutoResearch is architecturally skeptical of itself: every pipeline stage has a producer agent and a critic agent that must debate through an [[OMC Meetings|OMC Meeting]] before output advances.

## Core Insight

The system's differentiator is **rigor, not automation**. LLMs are massively overconfident (49.71% accuracy with 39.25% calibration error). Existing tools (AI-Scientist, [[AutoResearchClaw]]) produce template-like, incremental papers. AutoResearch addresses this with [[Adversarial Pipeline|adversarial gates]] at every stage.

## Architecture

Built on the [[OneManCompany]] (OMC) backbone as a "Research Lab" company instance:
- **Research Director** (COO-level Talent) orchestrates the pipeline
- **9 Producer Talents** handle each [[Research Pipeline Stages|stage]]
- **1 Adversarial Critic Talent** reviews all stages via [[OMC Meetings]]
- **[[Calibrated Confidence]]** tracks gate decisions and enables self-improvement

## Pipeline Stages

1. Topic Refinement
2. Literature Survey (highest failure rate in the field)
3. Idea Generation (core differentiator: adversarial novelty assessment)
4. Methodology Design & Theory Inference
5. Experiment Design
6. Auto Experiment (optional, skippable for theoretical papers)
7. Result Analysis & Refinement (with PIVOT logic)
8. Paper Generation (LaTeX, NeurIPS/ICML/ICLR templates)
9. Self-Review & Quality Gate (3 independent reviewer agents)

## Frontend

Web dashboard with Ivy Collection-inspired palette (green #245A40, gold #C5A55A, cream #F5F3ED). V3 features breakpoint system, 6-tab action panel (Edit, Instruct, Override, Skip, Artifacts, Export), and OMC Meeting visualization.

## Status

Design phase complete. Frontend V3 (interactive prototype) shipped. Backend implementation pending (OMC setup + Talent creation).
