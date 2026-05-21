"""Structural tests for the Stage 6a code-implementation-runbook SKILL.

The runbook is the bridge between Stage 5's prose experiment plan and the
Stage 6b runner: it must translate the spec into Python, push code via
experiment-infra's fast_push_code.sh, verify the push, and write a receipt
mapping each IV/DV/parameter back to its implementation site. Improvisation
(mock data when spec said real, new IVs/DVs, language drift) is an
auto-REJECT failure on the critic side, so the producer-side runbook must
spell out the prohibitions in detail."""
from __future__ import annotations

from pathlib import Path


SKILLS_ROOT = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "onemancompany"
    / "default_skills"
)
RUNBOOK = SKILLS_ROOT / "code-implementation-runbook" / "SKILL.md"


class TestRunbookExists:
    def test_skill_folder_exists(self):
        assert RUNBOOK.parent.exists()

    def test_skill_md_exists(self):
        assert RUNBOOK.exists()

    def test_frontmatter_present(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        assert text.startswith("---\n"), "SKILL.md must open with YAML frontmatter"
        head = text.split("---", 2)[1]
        assert "name: code-implementation-runbook" in head
        assert "description:" in head
        assert "allowed-tools:" in head

    def test_allowed_tools_include_bash_read_write(self):
        """The runbook runs fast_push_code.sh (Bash), reads Stage 4/5 spec
        artifacts (Read), and writes code files locally (Write)."""
        text = RUNBOOK.read_text(encoding="utf-8")
        head = text.split("---", 2)[1]
        assert "Bash" in head, "Bash needed for fast_push_code.sh"
        assert "Read" in head, "Read needed for Stage 4/5 spec"
        assert "Write" in head, "Write needed for code files"


class TestRunbookBehaviour:
    """The prose contract — these assertions catch silent drift if someone
    refactors the SKILL.md and loses the load-bearing instructions."""

    def test_reads_stage_4_5_artifacts(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        assert "stage4_methodology_designer.md" in text, (
            "Runbook must name the Stage 4 methodology artifact it reads"
        )
        assert "stage5_experiment_designer.md" in text, (
            "Runbook must name the Stage 5 experiment design artifact"
        )
        assert "stage5_assignments.md" in text, (
            "Runbook must name the Stage 5 assignments table"
        )

    def test_uses_fast_push_code(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        assert "fast_push_code.sh" in text, (
            "Runbook must name fast_push_code.sh — the code-push entrypoint"
        )

    def test_verifies_push_via_fast_query_working_dir(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        assert "fast_query_working_dir.sh" in text, (
            "Runbook must verify pushed files via fast_query_working_dir.sh; "
            "a push-without-verify is the same as a not-pushed file"
        )

    def test_writes_implementation_receipt(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        assert "stage6_implementation_receipt.md" in text, (
            "Runbook must produce stage6_implementation_receipt.md so the "
            "code-quality critic has a structured artifact to grade"
        )

    def test_forbids_mock_data(self):
        """Mock/synthetic data when Stage 5 named real benchmarks is the
        worst possible failure — Stage 6a auto-REJECT. The runbook must
        warn the producer about this explicitly."""
        text = RUNBOOK.read_text(encoding="utf-8")
        lowered = text.lower()
        assert ("mock" in lowered or "synthetic" in lowered), (
            "Runbook must explicitly warn about mock/synthetic data"
        )
        assert "real benchmarks" in lowered or "real benchmark" in lowered, (
            "Runbook must contrast mock/synthetic with 'real benchmarks' so "
            "the producer knows when each is allowed"
        )

    def test_forbids_new_ivs_dvs(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        # Tolerate either IV or DV mention, with prohibition language.
        assert ("IV" in text or "DV" in text), (
            "Runbook must mention IVs/DVs to anchor the spec-fidelity rule"
        )
        lowered = text.lower()
        assert (
            "don't add" in lowered
            or "do not add" in lowered
            or "not in stage 4" in lowered
            or "not in stage 4/5" in lowered
            or "introduce" in lowered  # "Don't introduce IVs..."
        ), (
            "Runbook must use prohibition language around adding new IVs/DVs "
            "(e.g. 'don't add', 'do not introduce', 'not in Stage 4/5')"
        )

    def test_forbids_session_key_echo(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        assert "INFRA_SESSION_KEY" in text, (
            "Runbook must mention INFRA_SESSION_KEY so the producer knows "
            "not to echo it (mirrors experiment-infra contract)"
        )

    def test_documents_multi_implementer_handoff(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        lowered = text.lower()
        assert ("multi" in lowered or "multiple" in lowered), (
            "Runbook must document the multi-implementer hand-off case so "
            "parallel code-writers don't double-cover assignments"
        )

    def test_documents_degraded_mode(self):
        """When remote infra is unreachable, the producer must explicitly
        flag the gap rather than silently fail or fabricate."""
        text = RUNBOOK.read_text(encoding="utf-8")
        assert (
            "Degraded mode" in text
            or "degraded" in text.lower()
            or "DEGRADED" in text
        ), (
            "Runbook must document the degraded mode (no remote infra) flow"
        )


class TestRunbookOnboardingWiring:
    """Cross-check that the runbook is wired into onboarding for the
    code_implementer skill."""

    def test_listed_in_skill_required_runbooks_for_code_implementer(self):
        from onemancompany.agents.onboarding import _SKILL_REQUIRED_RUNBOOKS
        runbooks = _SKILL_REQUIRED_RUNBOOKS.get("code_implementer", [])
        assert "code-implementation-runbook" in runbooks, (
            "The code_implementer skill must auto-inject "
            "code-implementation-runbook on hire"
        )

    def test_code_implementer_also_gets_experiment_infra(self):
        """The implementer pushes code via fast_push_code.sh which lives in
        the experiment-infra runbook — both must land on the same hire."""
        from onemancompany.agents.onboarding import _SKILL_REQUIRED_RUNBOOKS
        runbooks = _SKILL_REQUIRED_RUNBOOKS.get("code_implementer", [])
        assert "experiment-infra" in runbooks, (
            "code_implementer must also receive experiment-infra so "
            "fast_push_code.sh + credentials resolve at runtime"
        )
