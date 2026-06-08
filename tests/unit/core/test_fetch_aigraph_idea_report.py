"""Tests for PipelineEngine._fetch_aigraph_idea_report's reliable primary path.

The Stage-3 deterministic bypass depends on this method returning the aigraph
report. The primary path is the one-shot ``fetch_stage3_bundle`` (research_e2e);
it must NOT rely on the ``aigraph_research_ideas`` asset tool being registered
(that registration was the retired #132 startup binding) — the registered tool
is only a fallback.
"""
from __future__ import annotations

from types import SimpleNamespace

import onemancompany.core.tool_registry as tr
from onemancompany.agents import aigraph_grounding as ag
from onemancompany.core.pipeline_engine import PipelineEngine

GROUNDED = "# Selected Hypotheses\nevidence: arxiv:2404.15574#c01\n" + ("x" * 600)


def _fake(topic="chain of thought reasoning", project_dir="/tmp"):
    ns = SimpleNamespace(topic=topic, state={}, project_dir=project_dir)
    # the method calls these side-effect helpers; stub them for the unit test
    ns._save_stage3_graph_artifacts = lambda b: None
    ns._stage3_markdown_with_trace = lambda b: b.markdown
    return ns


def test_primary_bundle_used_and_tool_not_consulted(monkeypatch):
    monkeypatch.setattr(
        ag, "fetch_stage3_bundle",
        lambda *a, **k: ag.Stage3Bundle(ok=True, source="research_e2e", status="done",
                                        markdown=GROUNDED, n_matched=36, n_total=78, strength="strong"),
    )

    def _boom(name):  # the fallback tool must not be touched when the bundle works
        raise AssertionError("registered tool should not be consulted on the primary path")

    monkeypatch.setattr(tr.tool_registry, "get_tool", _boom)
    assert PipelineEngine._fetch_aigraph_idea_report(_fake()) == GROUNDED


def test_falls_back_to_none_when_bundle_fails_and_no_tool(monkeypatch):
    monkeypatch.setattr(ag, "fetch_stage3_bundle",
                        lambda *a, **k: ag.Stage3Bundle(ok=False, error="conn refused"))
    monkeypatch.setattr(tr.tool_registry, "get_tool", lambda name: None)
    assert PipelineEngine._fetch_aigraph_idea_report(_fake()) is None


def test_weak_coverage_still_returns_report(monkeypatch):
    weak_md = "# Stage 3\n> Corpus coverage: weak (0/300 matched)\n" + ("y" * 600)
    monkeypatch.setattr(
        ag, "fetch_stage3_bundle",
        lambda *a, **k: ag.Stage3Bundle(ok=True, markdown=weak_md, strength="weak", n_matched=0, n_total=300),
    )
    monkeypatch.setattr(tr.tool_registry, "get_tool",
                        lambda name: (_ for _ in ()).throw(AssertionError("should not reach tool")))
    assert PipelineEngine._fetch_aigraph_idea_report(_fake("obscure topic")) == weak_md
