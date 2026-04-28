"""Causal session memory — the core graph structure + file-based storage.

This module is the foundation that v4 builds on:
  - models.py    → SessionNode, CausalEdge, DomainRecord data classes
  - storage.py   → Pure-file persistence (JSON + JSONL + MEMORY.md)
  - finalize.py  → LLM-based session summarization (one call per session)
  - recall.py    → Rule-based BFS retrieval helpers

v4 extends this with: BM25 scoring, reflect synthesis, memory classification,
conflict detection. See memento_v4.adapter for the full pipeline.
"""
from .models import (
    ArtifactRef,
    CausalEdge,
    DecisionRecord,
    DomainRecord,
    SessionNode,
    TranscriptRef,
)

__all__ = [
    "ArtifactRef",
    "CausalEdge",
    "DecisionRecord",
    "DomainRecord",
    "SessionNode",
    "TranscriptRef",
]
