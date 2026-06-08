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
        assert args["run"] == "arxiv-reasoning-v0.7-540p-thaw1"  # run pinned, not LLM-chosen
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
