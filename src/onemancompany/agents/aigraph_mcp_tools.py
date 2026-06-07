"""Resolve the Idea Generator employee id for aigraph (LCG) Stage-3 grounding.

#137 — dynamic id resolution. The earlier aigraph bridge hard-coded the Idea
Generator id as ``00008`` and only honoured ``AIGRAPH_IDEA_GENERATOR_ID`` when
it happened to be in the process env, so it silently scoped the aigraph tools
to the wrong employee (``00008`` is the Methodology Designer on the current
roster). This module resolves the id DYNAMICALLY and never falls back to a
stale id.

#132 / #130 — the MCP-at-startup binding was RETIRED, not rewritten. The
original ``register_aigraph_mcp_tools()`` connected to aigraph's MCP server
with langchain-mcp-adapters and registered its tools inside the app's startup
lifespan. That crashed uvicorn at startup (the streamable-http client teardown
raises an anyio "Attempted to exit cancel scope in a different task"), and it
was redundant: aigraph's read-only query capability already reaches Stage 3 two
non-crashing ways —

  * the ``aigraph_get_idea_report`` asset tool — lazy ``urllib`` JSON-RPC to the
    aigraph MCP (0-LLM, ~200 ms), declared on the Idea Generator in
    ``hire_list.json`` (``skills: [idea_generator]`` + ``tools: [...]``) with a
    system-prompt contract that writes the report verbatim to
    ``stage3_idea_generator.md``; and
  * the same-origin ``/aigraph/*`` REST proxy that feeds the browser orbit
    graph.

The reason Stage 3 can still come out ungrounded is #130 (declared talent
``tools:`` do not bind to the agent at runtime — specialised talents run as
generic placeholders), NOT a missing MCP startup binding. This resolver is the
single source of truth for *which employee is the Idea Generator*, for the #130
runtime-binding work to scope the aigraph tools to.
"""
from __future__ import annotations

import os

from loguru import logger

_IDEA_GENERATOR_SKILL = "idea_generator"


def resolve_idea_generator_id() -> str:
    """Resolve the Idea Generator employee id (#137).

    Priority:
      1. ``AIGRAPH_IDEA_GENERATOR_ID`` env (explicit override).
      2. The employee whose profile ``skills`` include ``idea_generator``.
      3. ``""`` — no Idea Generator on the roster; the caller scopes nothing
         rather than falling back to a bogus/stale id.
    """
    env_id = os.environ.get("AIGRAPH_IDEA_GENERATOR_ID", "").strip()
    if env_id:
        return env_id
    try:
        from onemancompany.core.config import load_employee_configs

        for emp_id, cfg in load_employee_configs().items():
            if _IDEA_GENERATOR_SKILL in (getattr(cfg, "skills", None) or []):
                logger.debug("[aigraph] resolved Idea Generator id={} by skill", emp_id)
                return emp_id
    except Exception as e:  # noqa: BLE001
        logger.warning("[aigraph] dynamic Idea Generator id resolution failed: {}", e)
    logger.warning(
        "[aigraph] no employee with skill '{}' on the roster", _IDEA_GENERATOR_SKILL
    )
    return ""
