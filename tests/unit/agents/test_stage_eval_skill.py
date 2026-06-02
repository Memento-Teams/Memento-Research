"""Structural tests for the advisory stage-eval SKILL + its wiring.

The Stage Eval Agent runs after each pipeline stage, builds a stage-specific
checklist, verifies it against workspace evidence, and writes an advisory
``stageN_eval_report.md``. It is advisory only — it must never gate."""
from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SKILLS_ROOT = REPO_ROOT / "src" / "onemancompany" / "default_skills"
RUNBOOK = SKILLS_ROOT / "stage-eval" / "SKILL.md"
HIRE_LIST = REPO_ROOT / "company" / "hire_list.json"


class TestRunbookExists:
    def test_skill_folder_exists(self):
        assert RUNBOOK.parent.exists()

    def test_skill_md_exists(self):
        assert RUNBOOK.exists()

    def test_frontmatter_present(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        assert text.startswith("---\n"), "SKILL.md must open with YAML frontmatter"
        head = text.split("---", 2)[1]
        assert "name: stage-eval" in head
        assert "description:" in head
        assert "allowed-tools:" in head

    def test_allowed_tools_include_read_and_write(self):
        """The eval agent reads the workspace and writes its report; it must
        not need (or claim) shell/edit access to deliverables."""
        head = RUNBOOK.read_text(encoding="utf-8").split("---", 2)[1]
        assert "Read" in head
        assert "Write" in head


class TestRunbookBehaviour:
    def test_is_advisory_and_non_gating(self):
        lowered = RUNBOOK.read_text(encoding="utf-8").lower()
        assert "advisory" in lowered
        assert "never gate" in lowered or "does not gate" in lowered or (
            "not gate" in lowered
        ), "Runbook must state it is non-gating"

    def test_writes_per_stage_report_file(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        assert "stageN_eval_report.md" in text or "stage{N}_eval_report.md" in text

    def test_has_output_format_section(self):
        assert "Output Format" in RUNBOOK.read_text(encoding="utf-8")

    def test_covers_key_stages(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        for label in ("Stage 2", "Stage 6", "Stage 8"):
            assert label in text, f"Runbook must define a checklist for {label}"

    def test_every_stage_has_a_checklist(self):
        """All 9 pipeline stages must have a checklist section with at least
        two concrete (C1/C2...) items — no stage left with a thin stub."""
        text = RUNBOOK.read_text(encoding="utf-8")
        for n in range(1, 10):
            header = f"### Stage {n} "
            assert header in text, f"missing checklist section for Stage {n}"
            section = text.split(header, 1)[1].split("### Stage ", 1)[0]
            assert section.count("- C") >= 2, (
                f"Stage {n} checklist must have >= 2 concrete items"
            )

    def test_literature_stage_checks_authenticity_breadth_count(self):
        lowered = RUNBOOK.read_text(encoding="utf-8").lower()
        assert "authenticit" in lowered  # authenticity / authentic
        assert "breadth" in lowered
        assert "recency" in lowered or "count" in lowered

    def test_experiment_stage_checks_real_run_and_fabrication(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        lowered = text.lower()
        assert "actually run" in lowered or "real run" in lowered
        assert "FABRICATED" in text, (
            "Stage 6 must flag numbers with no workspace evidence as FABRICATED"
        )

    def test_unverifiable_over_guessing(self):
        assert "UNVERIFIABLE" in RUNBOOK.read_text(encoding="utf-8")


class TestOnboardingWiring:
    def test_stage_eval_skill_maps_to_runbook(self):
        from onemancompany.agents.onboarding import _SKILL_REQUIRED_RUNBOOKS

        assert _SKILL_REQUIRED_RUNBOOKS.get("stage_eval") == ["stage-eval"]


class TestHireListEntry:
    def test_hire_list_is_valid_json(self):
        json.loads(HIRE_LIST.read_text(encoding="utf-8"))

    def test_eval_agent_talent_present_with_skill(self):
        roster = json.loads(HIRE_LIST.read_text(encoding="utf-8"))
        evals = [t for t in roster if t.get("talent_id") == "eval-agent"]
        assert len(evals) == 1, "exactly one eval-agent talent expected"
        assert "stage_eval" in evals[0].get("skills", []), (
            "eval-agent must carry the stage_eval skill so the trigger can "
            "find it via _find_employee_by_skill"
        )
