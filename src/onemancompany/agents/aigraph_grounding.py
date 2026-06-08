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
import os
import re
import threading
from dataclasses import dataclass

# Defaults — the URL is env-overridable so no bare IP is ever hard-coded.
# On the box, loopback resolves to the box's own (latest) aigraph deployment.
DEFAULT_MCP_URL = "http://127.0.0.1:8765/mcp/"
DEFAULT_RUN = "arxiv-reasoning-v0.7-540p-thaw1"
DEFAULT_K = 8
_TOOL = "get_idea_report"

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
