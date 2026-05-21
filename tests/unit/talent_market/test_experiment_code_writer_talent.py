"""Structural tests for the bundled Experiment Code Writer talent.

Validates the talent package layout matches what `load_talent_profile`,
`list_available_talents`, and the hire-time skill-copy path in
`onboarding.py` expect."""
from __future__ import annotations

import json
from pathlib import Path

import yaml


TALENTS_ROOT = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "onemancompany"
    / "talent_market"
    / "talents"
)
TALENT_DIR = TALENTS_ROOT / "experiment-code-writer"


class TestExperimentCodeWriterProfile:
    def test_directory_exists(self):
        assert TALENT_DIR.exists(), "experiment-code-writer talent must ship under talents/"

    def test_profile_yaml_valid(self):
        data = yaml.safe_load((TALENT_DIR / "profile.yaml").read_text(encoding="utf-8"))
        assert data["id"] == "experiment-code-writer"
        assert data["name"]
        assert data["role"]
        assert data["hosting"] == "company"

    def test_profile_declares_code_implementer_skill(self):
        data = yaml.safe_load((TALENT_DIR / "profile.yaml").read_text(encoding="utf-8"))
        assert "code_implementer" in data.get("skills", []), (
            "Talent must declare code_implementer skill so the onboarding "
            "mapping injects the code-implementation-runbook on hire."
        )

    def test_system_prompt_references_code_implementation_runbook(self):
        data = yaml.safe_load((TALENT_DIR / "profile.yaml").read_text(encoding="utf-8"))
        template = data.get("system_prompt_template", "")
        assert "code-implementation-runbook" in template, (
            "System prompt must point the agent at the code-implementation-runbook"
        )
        assert "load_skill" in template, (
            "System prompt should tell the agent how to load the runbook"
        )

    def test_system_prompt_emphasises_spec_fidelity(self):
        """The prompt must remind the agent that this is translation, not
        redesign — the critic auto-REJECTs improvisation."""
        data = yaml.safe_load((TALENT_DIR / "profile.yaml").read_text(encoding="utf-8"))
        template = data.get("system_prompt_template", "")
        lowered = template.lower()
        assert (
            "translation" in lowered
            or "spec" in lowered
            or "improvis" in lowered
            or "stage 5" in lowered
            or "stage 4" in lowered
        ), (
            "System prompt should reference spec-fidelity language (translation, "
            "spec, improvisation, Stage 4/5) so the agent knows the contract"
        )


class TestExperimentCodeWriterManifest:
    def test_manifest_valid_json(self):
        data = json.loads((TALENT_DIR / "manifest.json").read_text(encoding="utf-8"))
        assert data["id"] == "experiment-code-writer"
        assert data["hosting"] == "company"


class TestExperimentCodeWriterSkill:
    """The talent's own skills/code_implementer/ pointer skill — copied to
    the employee at hire time via the folder-based skill path in
    onboarding._copy_talent_assets."""

    SKILL = TALENT_DIR / "skills" / "code_implementer" / "SKILL.md"

    def test_skill_md_exists(self):
        assert self.SKILL.exists(), (
            "Folder-based skill at skills/code_implementer/SKILL.md required "
            "so onboarding.py copies it via shutil.copytree at hire time."
        )

    def test_skill_md_frontmatter(self):
        text = self.SKILL.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        head = text.split("---", 2)[1]
        assert "name: code_implementer" in head

    def test_skill_md_points_at_code_implementation_runbook(self):
        text = self.SKILL.read_text(encoding="utf-8")
        assert "code-implementation-runbook" in text
        assert 'load_skill("code-implementation-runbook")' in text


class TestExperimentCodeWriterWiring:
    """Cross-check between this talent and the onboarding runbook mapping."""

    def test_talent_skill_key_matches_onboarding_mapping(self):
        """The skill key declared in profile.yaml must match the
        `_SKILL_REQUIRED_RUNBOOKS` key, otherwise code-implementation-runbook
        won't be injected when the talent is hired."""
        from onemancompany.agents.onboarding import _SKILL_REQUIRED_RUNBOOKS

        data = yaml.safe_load((TALENT_DIR / "profile.yaml").read_text(encoding="utf-8"))
        for skill_key in data["skills"]:
            if skill_key == "code_implementer":
                assert skill_key in _SKILL_REQUIRED_RUNBOOKS
                assert "code-implementation-runbook" in _SKILL_REQUIRED_RUNBOOKS[skill_key]
                return
        assert False, "code_implementer skill key not in profile.yaml"

    def test_list_available_talents_includes_experiment_code_writer(self):
        from onemancompany.core.config import list_available_talents

        ids = {t["id"] for t in list_available_talents()}
        assert "experiment-code-writer" in ids, (
            "Talent loader must discover experiment-code-writer via "
            "list_available_talents() so HR can hire it."
        )

    def test_load_talent_profile_returns_experiment_code_writer(self):
        from onemancompany.core.config import load_talent_profile

        data = load_talent_profile("experiment-code-writer")
        assert data, "load_talent_profile must find this talent"
        assert data["id"] == "experiment-code-writer"
