"""Register aigraph (literature-conflict-graph) MCP tools into the OMC
tool registry, scoped to the Idea Generator employee (Stage 3).

aigraph ships its own MCP server (streamable-http). We connect with
langchain-mcp-adapters, pull the read-only query tools, and register them
as talent/asset tools authorized only for the Idea Generator. The Idea
Generator then calls the MCP tool ``get_idea_report`` to produce the
Stage 3 ``# Selected Hypotheses`` deliverable (replacing the old bash
``lcg_query.sh`` path).

Wiring (#132): ``register_aigraph_mcp_tools()`` is invoked once at
startup, right after ``tool_registry.load_asset_tools()`` (see main.py).
The aigraph MCP server MUST be running before OMC starts (default
``http://localhost:8765/mcp/``, override with ``AIGRAPH_MCP_URL``). If it
is unreachable, registration is skipped with a warning and Stage 3 falls
back to whatever the SKILL describes — startup is never broken.

Idea Generator id (#137): resolved DYNAMICALLY at registration time —
``AIGRAPH_IDEA_GENERATOR_ID`` env wins, else the employee whose profile
``skills`` include ``idea_generator``. We never silently fall back to a
stale hard-coded id (the old ``00008`` default scoped the tools to the
wrong employee whenever the env wasn't loaded into the process).
"""
from __future__ import annotations

import os

from loguru import logger

# Read-only, 0-LLM tools we expose to the agent. We deliberately omit the
# paid run-trigger tools (start_run / get_run_status).
WANTED_TOOLS = {
    "get_idea_report",      # rendered Stage 3 markdown deliverable
    "query_hypotheses",     # structured top-K hypotheses
    "list_runs",            # discover available runs
    "get_run_summary",      # run metadata + anomaly histogram
    "get_conflict_graph",   # D3 {nodes, edges}
    "generate_ideas",       # cascade idea generator (non-empty guarantee)
    "research_ideas",       # one-shot: topic to ideas (reuse matching corpus)
}

_IDEA_GENERATOR_SKILL = "idea_generator"


def _aigraph_mcp_url() -> str:
    """aigraph MCP endpoint, read at call time so it stays configurable."""
    return os.environ.get("AIGRAPH_MCP_URL", "http://localhost:8765/mcp/")


def resolve_idea_generator_id() -> str:
    """Resolve the Idea Generator employee id (#137).

    Priority:
      1. ``AIGRAPH_IDEA_GENERATOR_ID`` env (explicit override).
      2. The employee whose profile ``skills`` include ``idea_generator``.
      3. "" — no Idea Generator on the roster; caller skips registration
         rather than scoping the tools to a bogus/stale id.
    """
    env_id = os.environ.get("AIGRAPH_IDEA_GENERATOR_ID", "").strip()
    if env_id:
        return env_id
    try:
        from onemancompany.core.config import load_employee_configs

        for emp_id, cfg in load_employee_configs().items():
            if _IDEA_GENERATOR_SKILL in (getattr(cfg, "skills", None) or []):
                logger.debug("[aigraph-mcp] resolved Idea Generator id={} by skill", emp_id)
                return emp_id
    except Exception as e:  # noqa: BLE001
        logger.warning("[aigraph-mcp] dynamic Idea Generator id resolution failed: {}", e)
    logger.warning(
        "[aigraph-mcp] no employee with skill '{}' on the roster — "
        "aigraph MCP tools NOT registered (Stage 3 falls back to the SKILL)",
        _IDEA_GENERATOR_SKILL,
    )
    return ""


def _load_mcp_tools(url: str) -> list:
    """Synchronously load the aigraph MCP tools as LangChain BaseTools.

    Works whether or not an event loop is already running (registration
    happens at startup, before uvicorn's loop, but be safe)."""
    import asyncio

    from langchain_mcp_adapters.client import MultiServerMCPClient

    def _sync() -> list:
        async def _aload() -> list:
            client = MultiServerMCPClient(
                {"aigraph": {"url": url, "transport": "streamable_http"}}
            )
            return await client.get_tools()

        return asyncio.run(_aload())

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _sync()
    # A loop is already running on this thread — run the load in a worker.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_sync).result()


def register_aigraph_mcp_tools() -> int:
    """Load + register the aigraph MCP tools for the Idea Generator.

    Returns the number of tools registered (0 if the server is down or no
    Idea Generator is on the roster). Never raises — Stage 3 wiring must
    not break OMC startup."""
    from onemancompany.core.tool_registry import ToolMeta, tool_registry

    idea_id = resolve_idea_generator_id()
    if not idea_id:
        return 0

    url = _aigraph_mcp_url()
    try:
        tools = _load_mcp_tools(url)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[aigraph-mcp] could not load MCP tools from {} ({}); "
            "Stage 3 MCP tools NOT registered", url, e
        )
        return 0

    n = 0
    for t in tools:
        if t.name not in WANTED_TOOLS:
            continue
        tool_registry.register(
            t,
            ToolMeta(
                name=t.name,
                category="asset",
                source="talent",
                allowed_users=[idea_id],
            ),
        )
        n += 1

    if n:
        logger.info(
            "[aigraph-mcp] registered {} MCP tool(s) for Idea Generator {} from {}",
            n, idea_id, url,
        )
    else:
        logger.warning(
            "[aigraph-mcp] MCP server reachable at {} but no wanted tools found", url
        )
    return n
