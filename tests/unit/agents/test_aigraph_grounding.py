"""Unit tests for the deterministic aigraph grounding fetch (Stage 3).

These cover the pure parsing/gating logic and the thread-isolated
``fetch_idea_report`` wrapper with the live MCP call mocked out, so they run
offline with no aigraph server. A live end-to-end call against :8765 is
verified separately.
"""
from __future__ import annotations

import pytest

from onemancompany.agents import aigraph_grounding as ag


# ----- coverage banner parsing ------------------------------------------------

STRONG_MD = (
    "# Stage 3: Idea Generation — chain of thought reasoning\n\n"
    "> **Corpus coverage: strong** (36/78 hypotheses matched, top relevance 3). "
    "the corpus covers this topic well.\n\n"
    "# Selected Hypotheses\n\n### Anomaly a005 — impact_conflict\n"
    "evidence: arxiv:2404.15574#c01\n"
)

WEAK_MD = (
    "# Stage 3: Idea Generation — quantum basket weaving\n\n"
    "> **Corpus coverage: weak** (0/300 hypotheses matched, top relevance 0). "
    "_No matches_ for this topic.\n"
)


def test_parse_coverage_strong():
    cov = ag._parse_coverage(STRONG_MD)
    assert cov == {"strength": "strong", "n_matched": 36, "n_total": 78, "top_relevance": 3}


def test_parse_coverage_weak():
    cov = ag._parse_coverage(WEAK_MD)
    assert cov["strength"] == "weak"
    assert cov["n_matched"] == 0
    assert cov["n_total"] == 300


def test_parse_coverage_absent():
    assert ag._parse_coverage("no banner here") == {}
    assert ag._parse_coverage("") == {}


# ----- Grounding gating logic -------------------------------------------------

def test_is_grounded_true_for_strong_with_arxiv():
    g = ag.Grounding(ok=True, markdown=STRONG_MD, strength="strong", n_matched=36, n_total=78)
    assert g.is_grounded is True
    assert g.is_weak is False


def test_is_weak_for_zero_match():
    g = ag.Grounding(ok=True, markdown=WEAK_MD, strength="weak", n_matched=0, n_total=300)
    assert g.is_weak is True
    assert g.is_grounded is False


def test_not_grounded_without_arxiv_even_if_matched():
    # matched hypotheses but no arxiv claim citations -> not a real grounded report
    g = ag.Grounding(ok=True, markdown="# Selected Hypotheses\n(no cites)", strength="strong", n_matched=5)
    assert g.is_grounded is False


def test_not_grounded_when_call_failed():
    g = ag.Grounding(ok=False, error="boom")
    assert g.is_grounded is False
    assert g.is_weak is False


# ----- fetch_idea_report wrapper (MCP call mocked) ----------------------------

def test_fetch_empty_topic_short_circuits():
    g = ag.fetch_idea_report("   ")
    assert g.ok is False
    assert g.error == "empty topic"


def test_fetch_success_parses_coverage(monkeypatch):
    async def fake_call(topic, run, k, url):
        assert topic == "chain of thought reasoning"
        assert run == ag.DEFAULT_RUN
        assert k == ag.DEFAULT_K
        return False, STRONG_MD

    monkeypatch.setattr(ag, "_call_async", fake_call)
    g = ag.fetch_idea_report("chain of thought reasoning")
    assert g.ok is True
    assert g.is_grounded is True
    assert (g.strength, g.n_matched, g.n_total, g.top_relevance) == ("strong", 36, 78, 3)


def test_fetch_tool_iserror_returns_not_ok(monkeypatch):
    async def fake_call(topic, run, k, url):
        return True, "boom report"

    monkeypatch.setattr(ag, "_call_async", fake_call)
    g = ag.fetch_idea_report("anything")
    assert g.ok is False
    assert "isError" in g.error


def test_fetch_exception_is_swallowed(monkeypatch):
    async def fake_call(topic, run, k, url):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(ag, "_call_async", fake_call)
    g = ag.fetch_idea_report("anything")
    assert g.ok is False
    assert "RuntimeError" in g.error and "connection refused" in g.error


def test_fetch_url_env_override(monkeypatch):
    seen = {}

    async def fake_call(topic, run, k, url):
        seen["url"] = url
        return False, STRONG_MD

    monkeypatch.setattr(ag, "_call_async", fake_call)
    monkeypatch.setenv("AIGRAPH_MCP_URL", "http://example.invalid:9999/mcp/")
    ag.fetch_idea_report("topic")
    assert seen["url"] == "http://example.invalid:9999/mcp/"
