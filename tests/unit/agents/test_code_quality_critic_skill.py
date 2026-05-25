"""Structural tests for the Stage 6a code-quality-critic SKILL.

The critic grades stage6_implementation_receipt.md against the Stage 4/5
prose spec. The single failure mode it must catch is **improvisation**:
the implementer adding variables, changing aggregation, or substituting
mock data for real benchmarks. Mock data when the spec said real
benchmarks is auto-REJECT; new IVs/DVs are auto-REJECT; non-English
receipt is auto-REJECT."""
from __future__ import annotations

from pathlib import Path


SKILLS_ROOT = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "onemancompany"
    / "default_skills"
)
RUNBOOK = SKILLS_ROOT / "code-quality-critic" / "SKILL.md"


class TestRunbookExists:
    def test_skill_folder_exists(self):
        assert RUNBOOK.parent.exists()

    def test_skill_md_exists(self):
        assert RUNBOOK.exists()

    def test_frontmatter_present(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        assert text.startswith("---\n"), "SKILL.md must open with YAML frontmatter"
        head = text.split("---", 2)[1]
        assert "name: code-quality-critic" in head
        assert "description:" in head
        assert "allowed-tools:" in head

    def test_allowed_tools_include_bash_and_read(self):
        """The critic runs ast.parse + fast_query_working_dir (Bash) and
        reads receipts + source files (Read)."""
        text = RUNBOOK.read_text(encoding="utf-8")
        head = text.split("---", 2)[1]
        assert "Bash" in head, "Bash needed for ast.parse + fast_query_working_dir"
        assert "Read" in head, "Read needed for receipt + Stage 4/5 + code files"


class TestRunbookBehaviour:
    """The prose contract — these assertions catch silent drift if someone
    refactors the SKILL.md and loses the load-bearing checks."""

    def test_lists_d1_through_d10(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        for i in range(1, 11):
            label = f"D{i} "
            assert label in text, (
                f"Stage 6a critic must define dimension {label.strip()} "
                f"explicitly"
            )

    def test_d1_is_spec_coverage(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        idx = text.find("D1")
        assert idx != -1, "D1 must be defined in the critic"
        window = text[idx:idx + 400]
        assert "Spec Coverage" in window, (
            "D1 must be the Spec Coverage dimension — the load-bearing "
            "check that every Stage 4/5 IV/DV/parameter is implemented"
        )

    def test_d2_is_no_improvisation(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        idx = text.find("D2")
        assert idx != -1, "D2 must be defined"
        window = text[idx:idx + 400]
        assert "No Improvisation" in window or "no improvisation" in window.lower(), (
            "D2 must be the No Improvisation dimension"
        )

    def test_d3_real_benchmarks_check(self):
        """D3 is the mock-vs-real benchmark check. Auto-REJECT trigger
        when spec named real datasets and code embeds a synthetic list."""
        text = RUNBOOK.read_text(encoding="utf-8")
        idx = text.find("D3")
        assert idx != -1, "D3 must be defined"
        window = text[idx:idx + 600]
        assert "Real Benchmarks" in window or "real benchmark" in window.lower(), (
            "D3 must enforce real-benchmarks vs mock-data fidelity"
        )

    def test_d4_verifies_remote_push(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        # The critic must actually rerun fast_query_working_dir.sh to
        # confirm pushed files exist on remote — receipt claims are not
        # trustworthy on their own.
        assert "fast_query_working_dir" in text, (
            "Critic must independently verify pushed files via "
            "fast_query_working_dir.sh — not just trust the receipt"
        )

    def test_d5_syntax_check_via_ast(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        # D5 enforces runnability by parsing each .py file via ast.
        assert ("ast.parse" in text or "python3 -c" in text), (
            "Critic must run ast.parse (or python3 -c) on each .py file "
            "to catch syntax errors before the runner discovers them"
        )

    def test_three_auto_reject_triggers(self):
        """The decision rule must list the three Stage 6a auto-REJECT
        triggers: mock data, new IVs/DVs, non-English receipt."""
        text = RUNBOOK.read_text(encoding="utf-8")
        lowered = text.lower()
        # (a) Mock / synthetic data when spec said real.
        assert ("mock" in lowered or "synthetic" in lowered), (
            "Auto-REJECT triggers must mention mock/synthetic data"
        )
        # (b) New IVs or DVs.
        assert ("iv" in lowered or "dv" in lowered), (
            "Auto-REJECT triggers must mention new IVs/DVs"
        )
        # (c) Non-English.
        assert ("non-english" in lowered or "non english" in lowered), (
            "Auto-REJECT triggers must mention non-English output"
        )

    def test_d1_d5_are_hard_gates(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        # Hard-gate dimension list may have been extended (D11 for smoke
        # mode, D12 for chat-template discipline). The base assertion is
        # that D1–D5 remain in the hard-gate set, however phrased.
        has_pass_list = (
            "D1, D2, D3, D4, D5 must PASS" in text
            or "D1, D2, D3, D4, D5," in text  # D6+ extension to hard gates
        )
        has_range = "D1-D5 are hard gates" in text or "D1–D5 are hard gates" in text
        assert has_pass_list or has_range, (
            "Stage 6a critic must state that D1-D5 are hard gates / must "
            "PASS for an overall PASS (extensions like D11/D12 are allowed)"
        )

    def test_output_format_specified(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        assert "Output Format" in text, (
            "Stage 6a critic must specify an Output Format section so the "
            "gate review has a deterministic shape"
        )

    def test_decision_rule_specified(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        assert "Decision Rule" in text, (
            "Stage 6a critic must specify a Decision Rule section so the "
            "PASS/REJECT logic is unambiguous"
        )


class TestRunbookOnboardingWiring:
    """Cross-check that the critic runbook is wired into onboarding for
    the adversarial_review skill so the Stage 6a critic-side trigger
    resolves."""

    def test_listed_in_skill_required_runbooks_for_adversarial_review(self):
        from onemancompany.agents.onboarding import _SKILL_REQUIRED_RUNBOOKS
        runbooks = _SKILL_REQUIRED_RUNBOOKS.get("adversarial_review", [])
        assert "code-quality-critic" in runbooks, (
            "adversarial_review must auto-inject code-quality-critic so "
            "the Stage 6a impl_critic dispatch resolves"
        )
