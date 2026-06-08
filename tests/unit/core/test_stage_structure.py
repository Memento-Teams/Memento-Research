"""Stages 1 (topic_refiner) + 2 (literature_surveyor) are removed: aigraph grounds
Stage 3 over the arxiv corpus (subsumes the survey) and the raw keyword feeds
Stage 3 directly. The pipeline entry is Stage 3; ids are kept 3..9 and looked up
by id (not list position)."""
from __future__ import annotations

from types import SimpleNamespace

from onemancompany.core import pipeline_engine as pe
from onemancompany.core.pipeline_engine import PipelineEngine, STAGES, FIRST_STAGE_ID, LAST_STAGE_ID


def test_stage_list_is_3_to_9():
    assert [s["id"] for s in STAGES] == [3, 4, 5, 6, 7, 8, 9]
    assert FIRST_STAGE_ID == 3
    assert LAST_STAGE_ID == 9
    # survey + topic refinement are gone
    skills = {s["skill"] for s in STAGES}
    assert "topic_refiner" not in skills
    assert "literature_surveyor" not in skills
    assert STAGES[0]["skill"] == "idea_generator"  # entry


def test_stage_def_lookup_by_id():
    fake = SimpleNamespace(current_stage=3)
    assert PipelineEngine._stage_def(fake, 3)["skill"] == "idea_generator"
    assert PipelineEngine._stage_def(fake, 4)["skill"] == "methodology_designer"
    assert PipelineEngine._stage_def(fake, 8)["skill"] == "paper_writer"
    # removed stages resolve to {} (not an IndexError / wrong positional entry)
    assert PipelineEngine._stage_def(fake, 1) == {}
    assert PipelineEngine._stage_def(fake, 2) == {}


def test_stage_specific_ids_preserved():
    # downstream runbook special-casing keys on these ids — they must not shift
    by_skill = {s["skill"]: s["id"] for s in STAGES}
    assert by_skill["methodology_designer"] == 4
    assert by_skill["experimentalist"] == 6
    assert by_skill["paper_writer"] == 8
