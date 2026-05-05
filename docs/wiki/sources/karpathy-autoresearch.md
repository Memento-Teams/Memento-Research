---
title: karpathy/autoresearch
type: source
created: 2026-04-24
updated: 2026-04-24
sources: []
tags: [competitor, ml-training, karpathy]
---

# karpathy/autoresearch

**GitHub:** github.com/karpathy/autoresearch | **Stars:** ~76K | **Created:** March 2026

## Summary

Autonomous AI research agent that iterates on LLM training code overnight. Modifies a single file (`train.py`), runs a 5-minute training experiment on one GPU, checks if validation loss improved, keeps or discards, loops forever.

## Architecture

- 3 files: `prepare.py` (data/eval), `train.py` (model/optimizer), `program.md` (agent instructions)
- Agent-agnostic: instructions consumed by Claude, Codex, or any coding agent
- No LLM API calls in code; the LLM IS the outer loop

## What It Automates

Hypothesis generation, code modification, experiment execution, result evaluation, accept/reject decision.

## What It Does NOT Automate

Literature review, paper writing, multi-agent collaboration, novelty assessment.

## Relevance to AutoResearch

Inspired the name and the concept of automated experiment iteration. But scope is narrow: single-file, single-GPU, no paper output. AutoResearch extends this to a full [[Research Pipeline Stages|9-stage pipeline]] with [[Adversarial Pipeline|adversarial review]].

> [!tip] Connection
> The Karpathy approach validates that AI agents CAN iterate on research code effectively. AutoResearch builds on this by adding the missing stages (literature, ideas, methodology, paper writing) and the adversarial quality layer.
