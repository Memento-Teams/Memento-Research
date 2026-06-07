"""Register aigraph (literature-conflict-graph) MCP tools into the OMC
tool registry, scoped to the Idea Generator employee (Stage 3).

aigraph ships its own MCP server (streamable-http). We connect with
langchain-mcp-adapters, pull the read-only query tools, and register them as
talent/asset tools authorized only for the Idea Generator. This replaces the
old bash `lcg_query.sh` path: the Idea Generator calls the MCP tool
`get_idea_report` to produce the Stage 3 `# Selected Hypotheses` deliverable.

The aigraph MCP server must be reachable from this host (default
http://localhost:8765/mcp/ — point AIGRAPH_MCP_URL at your serve's MCP, e.g.
via an SSH tunnel). If it is unreachable, registration is skipped with a
warning and Stage 3 falls back to whatever the SKILL describes — it never
raises, so OMC startup is unaffected.

Wire it once at startup, AFTER tool_registry.load_asset_tools(), e.g.::

    from onemancompany.agents.aigraph_mcp_tools import register_aigraph_mcp_tools
    register_aigraph_mcp_tools()
"""
from __future__ import annotations

import os

from loguru import logger

# Point this at the aigraph serve's MCP endpoint reachable from this host.
AIGRAPH_MCP_URL = os.environ.get("AIGRAPH_MCP_URL", "http://localhost:8765/mcp/")

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


def _resolve_idea_generator_id() -> str:
    """Employee id of the Idea Generator talent.

    Env override (AIGRAPH_IDEA_GENERATOR_ID) wins; otherwise resolve by
    ``talent_id == "idea-generator"`` so we never hardcode a roster slot — the
    hired id is assigned at hire time and is NOT stably ``00008`` (that slot was
    the Methodology Designer on at least one deploy)."""
    override = os.environ.get("AIGRAPH_IDEA_GENERATOR_ID")
    if override:
        return override
    try:
        import yaml
        from onemancompany.core.config import (
            EMPLOYEES_DIR, PROFILE_FILENAME, open_utf,
        )
        for d in sorted(EMPLOYEES_DIR.iterdir()):
            pf = d / PROFILE_FILENAME
            if not pf.exists():
                continue
            with open_utf(pf) as f:
                prof = yaml.safe_load(f) or {}
            if prof.get("talent_id") == "idea-generator":
                return prof.get("employee_id") or d.name
    except Exception as e:  # noqa: BLE001
        logger.debug("[aigraph-mcp] idea-generator id resolve failed: {}", e)
    return "00008"


def _load_mcp_tools() -> list:
    """Load the aigraph MCP tools as LangChain BaseTools.

    Works whether or not an event loop is already running (registration may
    happen inside uvicorn's lifespan loop)."""
    import asyncio

    from langchain_mcp_adapters.client import MultiServerMCPClient

    def _sync() -> list:
        async def _aload() -> list:
            client = MultiServerMCPClient(
                {"aigraph": {"url": AIGRAPH_MCP_URL, "transport": "streamable_http"}}
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

    Returns the number of tools registered (0 if the server is down).
    Never raises — Stage 3 wiring must not break OMC startup."""
    from onemancompany.core.tool_registry import ToolMeta, tool_registry

    idea_id = _resolve_idea_generator_id()
    try:
        tools = _load_mcp_tools()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[aigraph-mcp] could not load MCP tools from {} ({}); "
            "Stage 3 MCP tools NOT registered", AIGRAPH_MCP_URL, e
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
            "[aigraph-mcp] registered {} MCP tool(s) for employee {} from {}",
            n, idea_id, AIGRAPH_MCP_URL,
        )
    else:
        logger.warning("[aigraph-mcp] MCP server reachable but no wanted tools found")
    return n
