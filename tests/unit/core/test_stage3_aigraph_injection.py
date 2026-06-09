"""Deterministic Stage-3 aigraph grounding.

The Idea Generator LLM (MiniMax-M2.7) was observed skipping the
``aigraph_get_idea_report`` tool entirely and fabricating ``#claim-N``
citations from the Stage-2 survey. The engine now fetches the report itself
(reliable 0-LLM direct call) and injects it. These tests pin the fetch helper:
it returns the report on success and degrades to None (never raises, never
fabricates) on every failure mode.
"""
from __future__ import annotations

from unittest.mock import patch

from onemancompany.core.pipeline_engine import PipelineEngine

_PATCH = "onemancompany.core.tool_registry.tool_registry.get_tool"


class _FakeTool:
    def __init__(self, ret=None, exc=None):
        self._ret, self._exc = ret, exc

    def invoke(self, args):
        assert args["topic"]                 # research_ideas is topic-driven
        assert args.get("reuse") is True     # reuse matching corpus (no paid build)
        assert args.get("as_markdown") is True
        if self._exc:
            raise self._exc
        return self._ret


def _engine():
    return PipelineEngine("p_test", "/tmp/nonexistent_proj", "multi-agent debate for LLM reasoning")


def test_returns_report_when_tool_succeeds():
    big = "# Stage 3: Idea Generation — x\n" + ("a" * 600)
    with patch(_PATCH, return_value=_FakeTool(ret=big)):
        assert _engine()._fetch_aigraph_idea_report() == big


def test_none_when_tool_unregistered():
    with patch(_PATCH, return_value=None):
        assert _engine()._fetch_aigraph_idea_report() is None


def test_none_when_invoke_raises():
    with patch(_PATCH, return_value=_FakeTool(exc=RuntimeError("boom"))):
        assert _engine()._fetch_aigraph_idea_report() is None


def test_none_when_report_too_short():
    with patch(_PATCH, return_value=_FakeTool(ret="too short to be a real 71KB report")):
        assert _engine()._fetch_aigraph_idea_report() is None


def test_dict_result_is_unwrapped():
    big = "x" * 600
    with patch(_PATCH, return_value=_FakeTool(ret={"result": big})):
        assert _engine()._fetch_aigraph_idea_report() == big


def test_research_ideas_markdown_envelope_dict():
    """research_ideas returns {stats, ideas_markdown} — extract the markdown."""
    md = "# Ideas — x\n## 1. foo _(critic-conflict, conf=0.9)_\n" + ("a" * 600)
    ret = {"stats": {"n_ideas": 8}, "ideas_markdown": md}
    with patch(_PATCH, return_value=_FakeTool(ret=ret)):
        assert _engine()._fetch_aigraph_idea_report() == md


def test_research_ideas_markdown_envelope_json_string():
    """Same envelope arriving as a JSON string is parsed + unwrapped."""
    import json
    md = "# Ideas — x\n" + ("b" * 600)
    ret = json.dumps({"stats": {"n_ideas": 8}, "ideas_markdown": md})
    with patch(_PATCH, return_value=_FakeTool(ret=ret)):
        assert _engine()._fetch_aigraph_idea_report() == md


def test_stage3_injects_grounding_and_dispatches_producer(tmp_path, monkeypatch):
    """Stage 3 augmentation: the engine fetches the aigraph grounding, persists
    it (for the backstop), injects it verbatim into the producer prompt, and
    dispatches the producer to synthesise a pilot on top — it does NOT bypass the
    LLM or go straight to the critic. The injected report is the verbatim, no
    fabrication contract."""
    import onemancompany.core.pipeline_engine as pe

    eng = PipelineEngine("p_test", str(tmp_path), "multi-agent debate for LLM reasoning")
    report = "# Stage 3: Idea Generation — x\n# Selected Hypotheses\narxiv:2404.1#c1\n" + ("a" * 600)

    monkeypatch.setattr(eng, "_stage_def", lambda *a, **k: {"id": 3, "name": "Idea Generation", "skill": "idea_generator"})
    monkeypatch.setattr(eng, "_reset_attempt_timing", lambda: None)
    monkeypatch.setattr(eng, "_build_context", lambda: "ctx")
    monkeypatch.setattr(eng, "_retrieve_memory_guidance", lambda *a, **k: "")
    monkeypatch.setattr(eng, "_consume_pending_feedback", lambda: "")
    monkeypatch.setattr(eng, "_save", lambda: None)
    monkeypatch.setattr(eng, "_emit_stage_event", lambda *a, **k: None)
    monkeypatch.setattr(eng, "_fetch_aigraph_idea_report", lambda: report)
    monkeypatch.setattr(pe, "_find_employee_for_stage", lambda *a, **k: "00017")
    monkeypatch.setattr(pe, "load_employee_configs", lambda: {})

    calls = {}
    monkeypatch.setattr(eng, "_dispatch_critic", lambda r: calls.__setitem__("critic", r))
    monkeypatch.setattr(eng, "_dispatch_to_employee", lambda *a, **k: calls.__setitem__("producer", a))

    eng._dispatch_producer()

    # grounding persisted for the producer-complete backstop
    grounding = tmp_path / "aigraph_grounding.md"
    assert grounding.exists()
    assert grounding.read_text(encoding="utf-8") == report
    # producer IS dispatched, with the verbatim grounding injected into its prompt
    assert "producer" in calls
    assert report in calls["producer"][1]
    # critic is NOT called directly (the producer runs first, then the critic)
    assert "critic" not in calls


def test_stage3_falls_back_to_llm_when_aigraph_down(tmp_path, monkeypatch):
    """If aigraph can't be fetched, Stage 3 falls back to the LLM producer
    (with a no-fabrication contract) rather than writing nothing."""
    import onemancompany.core.pipeline_engine as pe

    eng = PipelineEngine("p_test", str(tmp_path), "topic")
    monkeypatch.setattr(eng, "_stage_def", lambda *a, **k: {"id": 3, "name": "Idea Generation", "skill": "idea_generator"})
    monkeypatch.setattr(eng, "_reset_attempt_timing", lambda: None)
    monkeypatch.setattr(eng, "_build_context", lambda: "ctx")
    monkeypatch.setattr(eng, "_retrieve_memory_guidance", lambda *a, **k: "")
    monkeypatch.setattr(eng, "_consume_pending_feedback", lambda: "")
    monkeypatch.setattr(eng, "_save", lambda: None)
    monkeypatch.setattr(eng, "_fetch_aigraph_idea_report", lambda: None)  # aigraph down
    monkeypatch.setattr(pe, "_find_employee_for_stage", lambda *a, **k: "00017")

    calls = {}
    monkeypatch.setattr(eng, "_dispatch_critic", lambda r: calls.__setitem__("critic", r))
    monkeypatch.setattr(eng, "_dispatch_to_employee", lambda *a, **k: calls.__setitem__("producer", a))

    eng._dispatch_producer()

    assert not (tmp_path / "stage3_idea_generator.md").exists()  # nothing hard-written
    assert "producer" in calls                                  # LLM producer dispatched
    assert "critic" not in calls
