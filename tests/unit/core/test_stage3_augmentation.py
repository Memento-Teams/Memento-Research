"""Tests for the Stage-3 augmentation backstop (``_ensure_stage3_grounded``).

In augmentation mode the engine injects the aigraph grounding and lets the
producer synthesise a pilot on top. The backstop guarantees the grounding lands
in the deliverable even if the producer drops it — using a lightweight fake
``self`` (the method only touches ``self.project_dir``).
"""
from __future__ import annotations

from types import SimpleNamespace

from onemancompany.core.pipeline_engine import PipelineEngine

GROUNDED_MD = (
    "# Stage 3: Idea Generation — chain of thought reasoning\n\n"
    "# Selected Hypotheses\n\n### Anomaly a005\nevidence: arxiv:2404.15574#c01\n"
)


def _fake(tmp_path):
    return SimpleNamespace(project_dir=str(tmp_path))


def test_grafts_grounding_when_producer_dropped_it(tmp_path):
    (tmp_path / "aigraph_grounding.md").write_text(GROUNDED_MD, encoding="utf-8")
    deliverable = tmp_path / "stage3_idea_generator.md"
    pilot_only = "## Primary Pilot Hypothesis\nH1: ... (no citations)\n"
    deliverable.write_text(pilot_only, encoding="utf-8")

    out = PipelineEngine._ensure_stage3_grounded(_fake(tmp_path), deliverable, pilot_only)
    # pilot preserved on top, grounding grafted below
    assert "Primary Pilot Hypothesis" in out
    assert "arxiv:2404.15574#c01" in out
    assert "# Selected Hypotheses" in out
    # file rewritten on disk too
    assert "arxiv:2404.15574#c01" in deliverable.read_text(encoding="utf-8")


def test_noop_when_producer_preserved_grounding(tmp_path):
    (tmp_path / "aigraph_grounding.md").write_text(GROUNDED_MD, encoding="utf-8")
    deliverable = tmp_path / "stage3_idea_generator.md"
    already = "## Primary Pilot Hypothesis\nH1...\n\n" + GROUNDED_MD
    deliverable.write_text(already, encoding="utf-8")
    out = PipelineEngine._ensure_stage3_grounded(_fake(tmp_path), deliverable, already)
    assert out == already  # untouched


def test_noop_when_no_grounding_file(tmp_path):
    deliverable = tmp_path / "stage3_idea_generator.md"
    res = "whatever the producer wrote"
    out = PipelineEngine._ensure_stage3_grounded(_fake(tmp_path), deliverable, res)
    assert out == res


def test_noop_when_grounding_weak_no_arxiv(tmp_path):
    (tmp_path / "aigraph_grounding.md").write_text("weak coverage, no cites", encoding="utf-8")
    deliverable = tmp_path / "stage3_idea_generator.md"
    res = "producer output"
    out = PipelineEngine._ensure_stage3_grounded(_fake(tmp_path), deliverable, res)
    assert out == res
