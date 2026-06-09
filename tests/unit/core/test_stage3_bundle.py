"""Tests for the one-shot research_e2e Stage-3 bundle + the engine's
graph-artifact persistence and provenance trace (live MCP mocked)."""
from __future__ import annotations

from types import SimpleNamespace

from onemancompany.agents import aigraph_grounding as ag
from onemancompany.core.pipeline_engine import PipelineEngine

DONE_PAYLOAD = {
    "status": "done",
    "idea_report_markdown": "# Selected Hypotheses\narxiv:2404.15574#c01\n",
    "ideas_markdown": "## Ideas\n- idea 1\n",
    "graph": {"nodes": [{"id": "h1"}], "edges": []},
    "graph_html": "<html>planet graph</html>",
    "coverage": {"n_matched": 14, "n_total": 78, "top_relevance": 3, "strength": "moderate"},
    "dashboard_url": "/dashboard/run123",
    "graph_url": "/dashboard/run123/graph",
    "run_id": "run123",
}


# ----- fetch_stage3_bundle (MCP layer mocked at _bundle_async) ----------------

def test_bundle_research_e2e_done(monkeypatch):
    async def fake(*a, **k):
        return "research_e2e", DONE_PAYLOAD
    monkeypatch.setattr(ag, "_bundle_async", fake)
    b = ag.fetch_stage3_bundle("chain of thought reasoning")
    assert b.ok and b.source == "research_e2e" and b.status == "done"
    assert b.is_grounded and "arxiv:" in b.markdown
    assert b.graph_html == "<html>planet graph</html>"
    assert b.graph == {"nodes": [{"id": "h1"}], "edges": []}
    assert (b.n_matched, b.n_total, b.top_relevance) == (14, 78, 3)
    assert b.dashboard_url == "/dashboard/run123"
    assert b.graph_url == "/dashboard/run123/graph"
    assert b.run_id == "run123"
    assert b.ideas_markdown.startswith("## Ideas")


def test_bundle_fallback_no_html(monkeypatch):
    async def fake(*a, **k):
        return "fallback", {"idea_report_markdown": "# Selected Hypotheses\narxiv:1#c1\n",
                            "graph": {"nodes": [], "edges": []}, "status": "done"}
    monkeypatch.setattr(ag, "_bundle_async", fake)
    b = ag.fetch_stage3_bundle("x")
    assert b.ok and b.source == "fallback" and b.is_grounded
    assert b.graph_html == ""               # fallback returns graph data, no html
    assert b.graph == {"nodes": [], "edges": []}


def test_bundle_empty_topic():
    assert ag.fetch_stage3_bundle("   ").ok is False


def test_bundle_no_markdown_is_not_ok(monkeypatch):
    async def fake(*a, **k):
        return "research_e2e", {"status": "done"}
    monkeypatch.setattr(ag, "_bundle_async", fake)
    assert ag.fetch_stage3_bundle("x").ok is False


# ----- engine graph-artifact persistence + provenance trace -------------------

def test_save_graph_artifacts_writes_html_and_json(tmp_path):
    b = ag.Stage3Bundle(ok=True, markdown="m", graph_html="<html>g</html>",
                        graph={"nodes": [], "edges": []}, dashboard_url="/d")
    PipelineEngine._save_stage3_graph_artifacts(SimpleNamespace(project_dir=str(tmp_path)), b)
    assert (tmp_path / "stage3_conflict_graph.html").read_text(encoding="utf-8") == "<html>g</html>"
    assert (tmp_path / "stage3_conflict_graph.json").exists()


def test_save_graph_artifacts_noop_without_html(tmp_path):
    b = ag.Stage3Bundle(ok=True, markdown="m")  # no graph_html, no graph
    PipelineEngine._save_stage3_graph_artifacts(SimpleNamespace(project_dir=str(tmp_path)), b)
    assert not (tmp_path / "stage3_conflict_graph.html").exists()


def test_markdown_trace_appends_provenance(tmp_path):
    b = ag.Stage3Bundle(ok=True, markdown="# Report\narxiv:1", source="research_e2e",
                        status="done", run_id="r1", n_matched=14, n_total=78,
                        top_relevance=3, dashboard_url="/dash", graph_url="/dash/graph")
    out = PipelineEngine._stage3_markdown_with_trace(SimpleNamespace(project_dir=str(tmp_path)), b)
    assert out.startswith("# Report")
    assert "aigraph provenance" in out
    assert "run_id=r1" in out and "coverage=14/78" in out
    assert "/dash" in out and "/dash/graph" in out
