---
title: Calibrated Confidence
type: concept
created: 2026-04-24
updated: 2026-04-24
sources: []
tags: [quality, metrics, calibration]
---

# Calibrated Confidence

A system for tracking how confident each agent is in its output, and whether that confidence matches reality. The goal: make the system's self-assessment trustworthy over time.

## Why

LLMs are overconfident. A 2025 study on GPT-4o found 49.71% accuracy with 39.25% Expected Calibration Error. The confidence the model expresses radically misaligns with actual correctness.

## How It Works

1. Every agent output includes a `confidence: float (0-1)` score
2. Scores are logged to SQLite with the stage, agent, and outcome
3. Gate logic: `pass=True AND confidence >= threshold` (default 0.6, configurable per stage)
4. If confidence is between 0.4-0.6 (uncertain zone), the orchestrator requests a second evaluation

## Frontend Visualization

- Confidence bars on each Meeting Card (green >= 65%, gold 50-65%, coral < 50%)
- Calibration tab in the right panel showing trajectory across stages
- Historical data: total runs, pass rate, retries, pivots

## Long-term Value

Over time, calibration data enables:
- Better gate thresholds (data-driven, not guessed)
- Identifying which stages/critics are most reliable
- Meta-research on the system's own performance
- Detecting systematic over/under-confidence per domain

> [!question] Open question
> How to bootstrap calibration when there's no historical data? Initial thresholds are hand-set at 0.6 and refined based on the first 10-20 runs.
