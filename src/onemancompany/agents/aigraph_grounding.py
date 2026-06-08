"""Deterministic aigraph grounding for Stage 3 (Idea Generation).

The pipeline calls aigraph's MCP ``get_idea_report`` tool DIRECTLY, in code —
NOT as an agent tool-call. Stage 3 grounding is therefore *guaranteed* rather
than left to the producer agent's discretion, which previously degraded to
hand-written hypotheses imitating the LCG format when the agent didn't (or
couldn't) invoke the tool (#130). Downstream, the LLM only *synthesises* a
single runnable pilot hypothesis on top of this verbatim, grounded report.

Why the MCP call is shaped this way
-----------------------------------
Opening the MCP streamable-http client enters an ``anyio`` cancel scope that
MUST be exited within the same task that entered it. We run the whole
``async with`` in a dedicated thread's fresh event loop via ``asyncio.run`` — the
client context closes *before* the loop tears down, in the *same* task, so the
#132 ``"Attempted to exit cancel scope in a different task"`` crash cannot occur.
Because the work happens in a separate thread with its own loop, this is safe to
call from sync code AND from inside uvicorn's already-running event loop.

This is the proven pattern: a standalone in-loop call to the live
``:8765/mcp`` server returns the full grounded ``get_idea_report`` markdown with
no anyio teardown error.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from dataclasses import dataclass, field

# Defaults — the URL is env-overridable so no bare IP is ever hard-coded.
# On the box, loopback resolves to the box's own (latest) aigraph deployment.
DEFAULT_MCP_URL = "http://127.0.0.1:8765/mcp/"
DEFAULT_RUN = "arxiv-reasoning-v0.7-540p-thaw1"
DEFAULT_K = 8
_TOOL = "get_idea_report"

# research_e2e (one-shot Stage-3 bundle) build handling. When a topic has no
# reusable corpus, research_e2e returns status="building" with a run_id; we poll
# get_run_status up to this budget, then re-call research_e2e(reuse=True). Set to
# 0 to skip building entirely and go straight to the fast fixed-run fallback.
DEFAULT_BUILD_WAIT = int(os.environ.get("AIGRAPH_BUILD_WAIT_SECONDS", "600"))
_POLL_INTERVAL = 15.0

# Matches the report's coverage banner, e.g.
#   "> **Corpus coverage: strong** (36/78 hypotheses matched, top relevance 3)."
_COVERAGE_RE = re.compile(
    r"Corpus\s+coverage:\s*\**\s*(?P<strength>\w+)\**\s*"
    r"\(\s*(?P<matched>\d+)\s*/\s*(?P<total>\d+)\s+hypotheses\s+matched"
    r"(?:\s*,\s*top\s+relevance\s*(?P<relevance>\d+))?",
    re.IGNORECASE,
)

# Coverage strengths we consider "weak" — caller should early-exit / switch
# corpus rather than feed an ungrounded report downstream.
_WEAK_STRENGTHS = {"weak", "none", "poor", "low"}


@dataclass
class Grounding:
    """Result of a deterministic aigraph grounding fetch.

    ``ok`` means the call succeeded; ``is_grounded`` is the stronger check the
    pipeline should gate on (real matched hypotheses + arxiv claim citations).
    Never raises — failures come back as ``ok=False`` with ``error`` set.
    """

    ok: bool
    markdown: str = ""
    strength: str = ""
    n_matched: int = 0
    n_total: int = 0
    top_relevance: int = 0
    error: str = ""

    @property
    def is_grounded(self) -> bool:
        """True iff the report is genuinely arxiv-grounded (not a weak/empty match)."""
        return (
            self.ok
            and self.n_matched > 0
            and self.strength not in _WEAK_STRENGTHS
            and "arxiv:" in self.markdown
        )

    @property
    def is_weak(self) -> bool:
        """True if aigraph itself flagged the corpus coverage as weak for this topic."""
        return self.ok and (self.strength in _WEAK_STRENGTHS or self.n_matched == 0)


def _parse_coverage(md: str) -> dict:
    m = _COVERAGE_RE.search(md or "")
    if not m:
        return {}
    return {
        "strength": (m.group("strength") or "").lower(),
        "n_matched": int(m.group("matched") or 0),
        "n_total": int(m.group("total") or 0),
        "top_relevance": int(m.group("relevance") or 0),
    }


async def _call_async(topic: str, run: str, k: int, url: str) -> tuple[bool, str]:
    # Imported lazily so importing this module never hard-requires the mcp SDK.
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(url) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool(
                _TOOL, {"topic": topic, "run": run, "k": k}
            )
            text = "".join(getattr(c, "text", "") or "" for c in res.content)
            return bool(getattr(res, "isError", False)), text


def fetch_idea_report(
    topic: str,
    run: str = DEFAULT_RUN,
    k: int = DEFAULT_K,
    url: str | None = None,
    timeout: float = 90.0,
) -> Grounding:
    """Deterministically fetch the aigraph Selected-Hypotheses report.

    Runs the MCP call in a dedicated thread (fresh event loop) so it is safe
    whether or not the caller is inside a running event loop. Never raises —
    returns ``Grounding(ok=False, error=...)`` on any failure so the pipeline
    can degrade gracefully instead of crashing the run.
    """
    url = url or os.environ.get("AIGRAPH_MCP_URL", DEFAULT_MCP_URL)
    topic = (topic or "").strip()
    if not topic:
        return Grounding(ok=False, error="empty topic")

    box: dict = {}

    def _worker() -> None:
        try:
            box["res"] = asyncio.run(_call_async(topic, run, k, url))
        except Exception as exc:  # noqa: BLE001 — surfaced via Grounding.error
            box["err"] = f"{type(exc).__name__}: {exc}"

    t = threading.Thread(target=_worker, name="aigraph-mcp", daemon=True)
    t.start()
    t.join(timeout=timeout + 5.0)

    if t.is_alive():
        return Grounding(ok=False, error=f"timeout after {timeout}s")
    if "err" in box:
        return Grounding(ok=False, error=box["err"])

    is_error, md = box["res"]
    if is_error:
        return Grounding(ok=False, markdown=md, error="aigraph tool returned isError")

    cov = _parse_coverage(md)
    return Grounding(
        ok=True,
        markdown=md,
        strength=cov.get("strength", ""),
        n_matched=cov.get("n_matched", 0),
        n_total=cov.get("n_total", 0),
        top_relevance=cov.get("top_relevance", 0),
    )


# ─────────────────────────────────────────────────────────────────────────────
# research_e2e — one-shot Stage-3 bundle (report + planet graph + ideas + links)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Stage3Bundle:
    """Everything Stage 3 needs from aigraph in one call.

    ``markdown`` is the verbatim Idea-Generation report (written to the
    deliverable). ``graph_html`` is a self-contained D3 "planet graph" page
    (saved as stage3_conflict_graph.html). ``dashboard_url``/``graph_url`` point
    back into the aigraph backend for traceability. Never-raises: a failure comes
    back as ``ok=False`` with ``error`` set.
    """

    ok: bool
    source: str = ""            # "research_e2e" | "fallback"
    status: str = ""            # done | building | error
    markdown: str = ""          # idea_report_markdown
    ideas_markdown: str = ""
    graph: dict = field(default_factory=dict)   # {nodes, edges}
    graph_html: str = ""
    strength: str = ""
    n_matched: int = 0
    n_total: int = 0
    top_relevance: int = 0
    dashboard_url: str = ""
    graph_url: str = ""
    run_id: str = ""
    error: str = ""

    @property
    def is_grounded(self) -> bool:
        return self.ok and bool(self.markdown) and "arxiv:" in self.markdown

    @property
    def is_weak(self) -> bool:
        return self.ok and (self.strength.lower() in _WEAK_STRENGTHS or self.n_matched == 0)


def _payload_from_result(res) -> tuple[dict | None, str]:
    """Pull a JSON object out of an MCP call result (structuredContent or text)."""
    sc = getattr(res, "structuredContent", None)
    if isinstance(sc, dict):
        # FastMCP wraps a non-dict return under {"result": ...}; unwrap a dict.
        if set(sc.keys()) == {"result"}:
            inner = sc["result"]
            if isinstance(inner, dict):
                return inner, ""
        else:
            return sc, ""
    text = "".join(getattr(c, "text", "") or "" for c in res.content)
    if text.strip().startswith("{"):
        try:
            return json.loads(text), text
        except Exception:  # noqa: BLE001
            return None, text
    return None, text


async def _bundle_async(topic, max_papers, min_ideas, k, run, url, build_wait):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(url) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            tool_names = {t.name for t in (await session.list_tools()).tools}

            async def call(name, args):
                res = await session.call_tool(name, args)
                payload, text = _payload_from_result(res)
                return payload, bool(getattr(res, "isError", False)), text

            # ---- primary: research_e2e (one-shot bundle) ----
            if "research_e2e" in tool_names:
                args = {"topic": topic, "max_papers": max_papers, "min_ideas": min_ideas,
                        "k": k, "reuse": True, "wait_seconds": 0}
                payload, _err, _ = await call("research_e2e", args)
                if isinstance(payload, dict):
                    status = (payload.get("status") or "").lower()
                    if status == "done":
                        return "research_e2e", payload
                    if status == "building" and payload.get("run_id") and build_wait > 0:
                        run_id = payload["run_id"]
                        waited = 0.0
                        while waited < build_wait:
                            await asyncio.sleep(_POLL_INTERVAL)
                            waited += _POLL_INTERVAL
                            sp, _s, _ = await call("get_run_status", {"run_id": run_id})
                            s_status = ((sp or {}).get("status") or "").lower() if isinstance(sp, dict) else ""
                            if s_status in ("done", "ready", "complete", "completed", "finished"):
                                p2, _e2, _ = await call("research_e2e", args)
                                if isinstance(p2, dict) and (p2.get("status") or "").lower() == "done":
                                    return "research_e2e", p2
                                break
                            if s_status in ("error", "failed", "cancelled", "canceled"):
                                break
                    # building timed out / errored → fall through to fallback

            # ---- fallback: get_idea_report + get_conflict_graph (old aigraph) ----
            report = ""
            graph: dict = {}
            if "get_idea_report" in tool_names:
                rp, _r, rtext = await call("get_idea_report", {"topic": topic, "run": run, "k": k})
                report = (rp.get("markdown") if isinstance(rp, dict) else "") or rtext or ""
            if "get_conflict_graph" in tool_names:
                gp, _g, _ = await call("get_conflict_graph", {"topic": topic, "run": run, "k": k})
                if isinstance(gp, dict):
                    graph = gp
            return "fallback", {"idea_report_markdown": report, "graph": graph, "status": "done"}


def fetch_stage3_bundle(
    topic: str,
    *,
    max_papers: int = 30,
    min_ideas: int = 5,
    k: int = DEFAULT_K,
    run: str = DEFAULT_RUN,
    url: str | None = None,
    build_wait: int | None = None,
) -> Stage3Bundle:
    """One-shot Stage-3 grounding bundle via aigraph ``research_e2e``.

    Tries ``research_e2e`` (report + planet-graph HTML + ideas + coverage +
    dashboard links); on ``status="building"`` it polls ``get_run_status`` up to
    ``build_wait`` seconds and re-calls; if research_e2e is absent (old aigraph)
    or the build doesn't finish, it falls back to ``get_idea_report`` +
    ``get_conflict_graph``. Thread-isolated and never-raises.
    """
    url = url or os.environ.get("AIGRAPH_MCP_URL", DEFAULT_MCP_URL)
    if build_wait is None:
        build_wait = DEFAULT_BUILD_WAIT
    topic = (topic or "").strip()
    if not topic:
        return Stage3Bundle(ok=False, error="empty topic")

    box: dict = {}

    def _worker() -> None:
        try:
            box["res"] = asyncio.run(
                _bundle_async(topic, max_papers, min_ideas, k, run, url, build_wait)
            )
        except Exception as exc:  # noqa: BLE001
            box["err"] = f"{type(exc).__name__}: {exc}"

    t = threading.Thread(target=_worker, name="aigraph-research-e2e", daemon=True)
    t.start()
    t.join(timeout=build_wait + 120.0)

    if t.is_alive():
        return Stage3Bundle(ok=False, error=f"timeout after ~{int(build_wait) + 120}s")
    if "err" in box:
        return Stage3Bundle(ok=False, error=box["err"])

    source, payload = box["res"]
    payload = payload or {}
    md = payload.get("idea_report_markdown", "") or ""
    cov = payload.get("coverage") or {}
    if not cov and md:
        cov = _parse_coverage(md)
    ideas_md = payload.get("ideas_markdown", "") or ""
    if not ideas_md and isinstance(payload.get("ideas"), str):
        ideas_md = payload["ideas"]
    return Stage3Bundle(
        ok=bool(md),
        source=source,
        status=(payload.get("status") or "done"),
        markdown=md,
        ideas_markdown=ideas_md,
        graph=payload.get("graph") or {},
        graph_html=payload.get("graph_html", "") or "",
        strength=str(cov.get("strength", "") or ""),
        n_matched=int(cov.get("n_matched", 0) or 0),
        n_total=int(cov.get("n_total", 0) or 0),
        top_relevance=int(cov.get("top_relevance", 0) or 0),
        dashboard_url=payload.get("dashboard_url", "") or "",
        graph_url=payload.get("graph_url", "") or "",
        run_id=payload.get("run_id", "") or "",
        error="" if md else "no idea_report_markdown returned",
    )
