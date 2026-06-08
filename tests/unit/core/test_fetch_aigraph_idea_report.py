"""Tests for PipelineEngine._fetch_aigraph_idea_report's reliable primary path.

The Stage-3 deterministic bypass depends on this method returning the aigraph
report. It must NOT rely on the ``aigraph_research_ideas`` asset tool being
registered (that registration was the retired #132 startup binding) — the
direct MCP call is the reliable primary path; the registered tool is fallback.
"""
from __future__ import annotations

from types import SimpleNamespace

import onemancompany.core.tool_registry as tr
from onemancompany.agents import aigraph_grounding as ag
from onemancompany.core.pipeline_engine import PipelineEngine

GROUNDED = "# Selected Hypotheses\nevidence: arxiv:2404.15574#c01\n" + ("x" * 600)


def _fake(topic="chain of thought reasoning"):
    return SimpleNamespace(topic=topic, state={})


def test_primary_direct_mcp_used_and_tool_not_consulted(monkeypatch):
    monkeypatch.setattr(
        ag, "fetch_idea_report",
        lambda *a, **k: ag.Grounding(ok=True, markdown=GROUNDED, strength="strong",
                                     n_matched=36, n_total=78),
    )

    def _boom(name):  # the fallback tool must not be touched when direct works
        raise AssertionError("registered tool should not be consulted on the primary path")

    monkeypatch.setattr(tr.tool_registry, "get_tool", _boom)
    out = PipelineEngine._fetch_aigraph_idea_report(_fake())
    assert out == GROUNDED


def test_falls_back_to_none_when_direct_fails_and_no_tool(monkeypatch):
    monkeypatch.setattr(ag, "fetch_idea_report",
                        lambda *a, **k: ag.Grounding(ok=False, error="conn refused"))
    monkeypatch.setattr(tr.tool_registry, "get_tool", lambda name: None)
    assert PipelineEngine._fetch_aigraph_idea_report(_fake()) is None


def test_weak_coverage_still_returns_report(monkeypatch):
    weak_md = "# Stage 3\n> Corpus coverage: weak (0/300 matched)\n" + ("y" * 600)
    monkeypatch.setattr(
        ag, "fetch_idea_report",
        lambda *a, **k: ag.Grounding(ok=True, markdown=weak_md, strength="weak",
                                     n_matched=0, n_total=300),
    )
    # weak path returns the report (carries the coverage banner) without needing the tool
    monkeypatch.setattr(tr.tool_registry, "get_tool",
                        lambda name: (_ for _ in ()).throw(AssertionError("should not reach tool")))
    assert PipelineEngine._fetch_aigraph_idea_report(_fake("obscure topic")) == weak_md
