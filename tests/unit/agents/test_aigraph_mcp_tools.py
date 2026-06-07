"""Unit tests for agents/aigraph_mcp_tools.py — Idea Generator id resolution.

Covers #137: the bridge must resolve the Idea Generator employee
dynamically and NEVER silently fall back to the stale hard-coded "00008".
"""
from __future__ import annotations

from unittest.mock import patch


class _Cfg:
    """Minimal stand-in for EmployeeConfig (only .skills is read)."""

    def __init__(self, skills):
        self.skills = skills


def test_env_override_wins(monkeypatch):
    from onemancompany.agents.aigraph_mcp_tools import resolve_idea_generator_id

    monkeypatch.setenv("AIGRAPH_IDEA_GENERATOR_ID", "00042")
    assert resolve_idea_generator_id() == "00042"


def test_dynamic_resolution_by_skill(monkeypatch):
    from onemancompany.agents import aigraph_mcp_tools as m

    monkeypatch.delenv("AIGRAPH_IDEA_GENERATOR_ID", raising=False)
    cfgs = {"00006": _Cfg(["topic_refiner"]), "00017": _Cfg(["idea_generator"])}
    with patch("onemancompany.core.config.load_employee_configs", return_value=cfgs):
        assert m.resolve_idea_generator_id() == "00017"


def test_no_idea_generator_returns_empty(monkeypatch):
    """No employee carries the idea_generator skill → "" (caller skips
    registration rather than scoping to a bogus id)."""
    from onemancompany.agents import aigraph_mcp_tools as m

    monkeypatch.delenv("AIGRAPH_IDEA_GENERATOR_ID", raising=False)
    cfgs = {"00006": _Cfg(["topic_refiner"])}
    with patch("onemancompany.core.config.load_employee_configs", return_value=cfgs):
        assert m.resolve_idea_generator_id() == ""


def test_never_returns_stale_00008(monkeypatch):
    """#137 regression guard: the old default was a hard-coded "00008".
    With no env and no resolvable employee we must NOT return it."""
    from onemancompany.agents import aigraph_mcp_tools as m

    monkeypatch.delenv("AIGRAPH_IDEA_GENERATOR_ID", raising=False)
    with patch("onemancompany.core.config.load_employee_configs", return_value={}):
        assert m.resolve_idea_generator_id() != "00008"


def test_resolution_failure_is_swallowed(monkeypatch):
    """A broken roster lookup must not raise — Stage 3 wiring must never
    break startup."""
    from onemancompany.agents import aigraph_mcp_tools as m

    monkeypatch.delenv("AIGRAPH_IDEA_GENERATOR_ID", raising=False)
    with patch("onemancompany.core.config.load_employee_configs", side_effect=RuntimeError("boom")):
        assert m.resolve_idea_generator_id() == ""
