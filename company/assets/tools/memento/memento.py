"""Memento memory asset tool.

Two LangChain @tool functions: store + recall. Each employee has a private
memory store under EMPLOYEES_DIR/{employee_id}/memory/. Isolation is
enforced server-side via _current_vessel ContextVar — employee_id is never
a tool parameter, so the LLM cannot address another employee's store.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.tools import tool
from loguru import logger

from onemancompany.core.config import EMPLOYEES_DIR
from onemancompany.core.memory import MemoryV4Adapter
from onemancompany.core.vessel import _current_vessel


_VALID_ROLES = {"user", "assistant"}


def _validate_turns(turns) -> str | None:
    """Return error message string, or None if turns is valid."""
    if not isinstance(turns, list):
        return "turns must be a non-empty list of {role, content} dicts"
    if not turns:
        return "turns must be a non-empty list of {role, content} dicts"
    for i, turn in enumerate(turns):
        if not isinstance(turn, dict):
            return f"turn {i}: must be a dict with 'role' and 'content'"
        role = turn.get("role")
        content = turn.get("content")
        if not role:
            return f"turn {i}: missing 'role'"
        if role not in _VALID_ROLES:
            return f"turn {i}: invalid role '{role}' (must be 'user' or 'assistant')"
        if not isinstance(content, str) or not content.strip():
            return f"turn {i}: missing or empty 'content'"
    return None


def _resolve_employee_id() -> str:
    vessel = _current_vessel.get(None)
    if vessel is None:
        raise RuntimeError("memento tools require an employee context")
    employee_id = getattr(vessel, "employee_id", "")
    if not employee_id:
        raise RuntimeError("memento tools require an employee context")
    return employee_id


def _employee_memory_dirs(employee_id: str) -> tuple[Path, Path]:
    mem_root = EMPLOYEES_DIR / employee_id / "memory"
    sessions_dir = mem_root / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return mem_root, sessions_dir


@tool
def store(turns: list[dict]) -> dict:
    """Persist a finished session into your private long-term memory.

    Call this when a chunk of work concludes — task done, decision made,
    important correction received. The session gets summarized via 1 LLM
    call into a SessionNode (title, goal, outcome, decisions, key quotes,
    files touched), wired into the causal graph (continues / contradicts
    edges to prior sessions), and conflicting facts get supersede flags.

    Args:
        turns: ordered conversation turns. Each turn:
            {"role": "user" | "assistant", "content": "..."}
            Pass enough recent dialogue to capture the substance — the
            finalizer extracts verbatim quotes, so include exact wording
            for decisions, numbers, names, and file paths.
    """
    try:
        employee_id = _resolve_employee_id()
    except RuntimeError as exc:
        return {"status": "error", "message": str(exc)}

    err = _validate_turns(turns)
    if err is not None:
        return {"status": "error", "message": err}

    return {"status": "error", "message": "not implemented yet"}


@tool
def recall(query: str, top_k: int = 5) -> dict:
    """Search your private long-term memory for sessions relevant to a query.

    Hybrid retrieval: vector similarity + BM25 lexical match + causal-chain
    BFS expansion (forward up to 5 hops, backward up to 2). Returns a
    markdown context block with the top-K session summaries, linked
    decisions, and supersede notes.

    Args:
        query: natural-language question or topic.
        top_k: how many top sessions to surface (1 to 20, default 5).
    """
    try:
        employee_id = _resolve_employee_id()
    except RuntimeError as exc:
        return {"status": "error", "message": str(exc)}

    return {"status": "error", "message": "not implemented yet"}
