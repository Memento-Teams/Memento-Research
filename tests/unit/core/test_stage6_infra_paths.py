"""Cover PipelineEngine._stage6_infra_paths (#156) — locating the
experiment-infra skill scripts + base config, env-first then glob fallback."""
from __future__ import annotations

from types import SimpleNamespace

from onemancompany.core.pipeline_engine import PipelineEngine

SCRIPTS = ("fast_push_code.sh", "fast_submit.sh", "fast_query_exp_status.sh")


def _mk_skill(root):
    sd = root / "skills" / "experiment-infra" / "scripts"
    sd.mkdir(parents=True)
    for n in SCRIPTS:
        (sd / n).write_text("#!/bin/sh\n")
    assets = root / "skills" / "experiment-infra" / "assets"
    assets.mkdir(parents=True)
    (assets / "base.conf.json").write_text("{}")
    return sd


def test_infra_paths_found_via_glob(tmp_path, monkeypatch):
    monkeypatch.delenv("EXPERIMENT_INFRA_SCRIPTS", raising=False)
    monkeypatch.delenv("STAGE6_INFRA_CONFIG", raising=False)
    _mk_skill(tmp_path)  # skill sits at an ancestor of project_dir
    proj = tmp_path / "a" / "iter_001"
    proj.mkdir(parents=True)
    scripts, config = PipelineEngine._stage6_infra_paths(SimpleNamespace(project_dir=str(proj)))
    assert set(scripts) == set(SCRIPTS)
    assert config.endswith("base.conf.json")


def test_infra_paths_via_env(tmp_path, monkeypatch):
    sd = _mk_skill(tmp_path)
    monkeypatch.setenv("EXPERIMENT_INFRA_SCRIPTS", str(sd))
    monkeypatch.setenv("STAGE6_INFRA_CONFIG", "/custom/base.conf.json")
    scripts, config = PipelineEngine._stage6_infra_paths(SimpleNamespace(project_dir=str(tmp_path)))
    assert set(scripts) == set(SCRIPTS)
    assert config == "/custom/base.conf.json"


def test_infra_paths_none_found(tmp_path, monkeypatch):
    monkeypatch.delenv("EXPERIMENT_INFRA_SCRIPTS", raising=False)
    monkeypatch.delenv("STAGE6_INFRA_CONFIG", raising=False)
    # mirror the real layout: a `company` dir stops the upward walk (so the glob
    # can't escape into sibling pytest tmp dirs); no skill dir present -> empty.
    base = tmp_path / "proj"
    (base / "company").mkdir(parents=True)
    proj = base / "iter_001"
    proj.mkdir(parents=True)
    scripts, config = PipelineEngine._stage6_infra_paths(SimpleNamespace(project_dir=str(proj)))
    assert scripts == {} and config == ""


def test_infra_paths_stops_at_company_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("EXPERIMENT_INFRA_SCRIPTS", raising=False)
    monkeypatch.delenv("STAGE6_INFRA_CONFIG", raising=False)
    # a `company` dir on an ancestor stops the upward walk before the skill above it
    _mk_skill(tmp_path)  # skill at tmp_path level
    mid = tmp_path / "a"
    (mid / "company").mkdir(parents=True)
    proj = mid / "iter_001"
    proj.mkdir(parents=True)
    scripts, _ = PipelineEngine._stage6_infra_paths(SimpleNamespace(project_dir=str(proj)))
    assert scripts == {}  # walk stopped at the company dir, never reached the skill
