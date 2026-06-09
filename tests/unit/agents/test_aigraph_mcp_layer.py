"""Cover the MCP transport layer of aigraph_grounding (the thread-isolated
``_call_async`` / ``_bundle_async``) with the streamable-http MCP client mocked,
plus ``_payload_from_result`` envelope handling. These run the async functions
directly (no real server) so coverage traces them."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from onemancompany.agents import aigraph_grounding as ag


# ----- fake MCP client/session ------------------------------------------------

class _FakeContent:
    def __init__(self, text):
        self.text = text


class _FakeResult:
    def __init__(self, content_text="", is_error=False, structured=None):
        self.content = [_FakeContent(content_text)] if content_text else []
        self.isError = is_error
        if structured is not None:
            self.structuredContent = structured


class _FakeTool:
    def __init__(self, name):
        self.name = name


class _FakeSession:
    """Scripted MCP session. ``responses`` maps tool name -> list of results
    (popped in order) or a single result."""

    def __init__(self, read, write, *, tool_names, responses):
        self._tool_names = tool_names
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def initialize(self):
        return None

    async def list_tools(self):
        return SimpleNamespace(tools=[_FakeTool(n) for n in self._tool_names])

    async def call_tool(self, name, args):
        r = self._responses[name]
        if isinstance(r, list):
            return r.pop(0)
        return r


def _fake_streams(url):
    class _CM:
        async def __aenter__(self):
            return (object(), object(), object())

        async def __aexit__(self, *a):
            return False

    return _CM()


def _install(monkeypatch, *, tool_names, responses):
    monkeypatch.setattr("mcp.client.streamable_http.streamablehttp_client", _fake_streams)
    monkeypatch.setattr(
        "mcp.ClientSession",
        lambda r, w: _FakeSession(r, w, tool_names=tool_names, responses=responses),
    )
    monkeypatch.setattr(ag, "_POLL_INTERVAL", 0.0)


# ----- _payload_from_result ---------------------------------------------------

def test_payload_from_structured_dict():
    res = _FakeResult(structured={"status": "done", "x": 1})
    payload, _ = ag._payload_from_result(res)
    assert payload == {"status": "done", "x": 1}


def test_payload_unwraps_result_envelope():
    res = _FakeResult(structured={"result": {"status": "done"}})
    payload, _ = ag._payload_from_result(res)
    assert payload == {"status": "done"}


def test_payload_from_text_json():
    res = _FakeResult(content_text='{"status": "done"}')
    payload, text = ag._payload_from_result(res)
    assert payload == {"status": "done"} and text == '{"status": "done"}'


def test_payload_from_text_non_json():
    res = _FakeResult(content_text="not json")
    payload, text = ag._payload_from_result(res)
    assert payload is None and text == "not json"


# ----- _call_async (get_idea_report) ------------------------------------------

@pytest.mark.asyncio
async def test_call_async_returns_text(monkeypatch):
    _install(monkeypatch, tool_names=["get_idea_report"],
             responses={"get_idea_report": _FakeResult(content_text="# report arxiv:1")})
    is_error, text = await ag._call_async("topic", ag.DEFAULT_RUN, 8, "http://x/mcp/")
    assert is_error is False and "arxiv:1" in text


# ----- _bundle_async ----------------------------------------------------------

@pytest.mark.asyncio
async def test_bundle_research_e2e_done_immediately(monkeypatch):
    done = _FakeResult(structured={"status": "done", "idea_report_markdown": "arxiv:1",
                                   "graph_html": "<g>", "coverage": {"n_matched": 5, "n_total": 9}})
    _install(monkeypatch, tool_names=["research_e2e", "get_run_status"],
             responses={"research_e2e": done})
    source, payload = await ag._bundle_async("t", 30, 5, 8, ag.DEFAULT_RUN, "http://x/mcp/", 600)
    assert source == "research_e2e" and payload["status"] == "done"
    assert payload["graph_html"] == "<g>"


@pytest.mark.asyncio
async def test_bundle_building_then_polls_to_done(monkeypatch):
    building = _FakeResult(structured={"status": "building", "run_id": "r1"})
    done = _FakeResult(structured={"status": "done", "idea_report_markdown": "arxiv:1"})
    _install(monkeypatch, tool_names=["research_e2e", "get_run_status"],
             responses={
                 "research_e2e": [building, done],          # 1st building, 2nd done
                 "get_run_status": _FakeResult(structured={"status": "done"}),
             })
    source, payload = await ag._bundle_async("t", 30, 5, 8, ag.DEFAULT_RUN, "http://x/mcp/", 600)
    assert source == "research_e2e" and payload["status"] == "done"


@pytest.mark.asyncio
async def test_bundle_building_error_falls_back(monkeypatch):
    building = _FakeResult(structured={"status": "building", "run_id": "r1"})
    _install(monkeypatch, tool_names=["research_e2e", "get_run_status", "get_idea_report", "get_conflict_graph"],
             responses={
                 "research_e2e": building,
                 "get_run_status": _FakeResult(structured={"status": "error"}),
                 "get_idea_report": _FakeResult(structured={"markdown": "arxiv:1 report"}),
                 "get_conflict_graph": _FakeResult(structured={"nodes": [], "edges": []}),
             })
    source, payload = await ag._bundle_async("t", 30, 5, 8, ag.DEFAULT_RUN, "http://x/mcp/", 600)
    assert source == "fallback"
    assert payload["idea_report_markdown"] == "arxiv:1 report"
    assert payload["graph"] == {"nodes": [], "edges": []}


@pytest.mark.asyncio
async def test_bundle_no_research_e2e_uses_fallback(monkeypatch):
    _install(monkeypatch, tool_names=["get_idea_report", "get_conflict_graph"],
             responses={
                 "get_idea_report": _FakeResult(content_text="arxiv:1 from text"),
                 "get_conflict_graph": _FakeResult(structured={"nodes": [{"id": "h1"}], "edges": []}),
             })
    source, payload = await ag._bundle_async("t", 30, 5, 8, ag.DEFAULT_RUN, "http://x/mcp/", 0)
    assert source == "fallback"
    assert "arxiv:1" in payload["idea_report_markdown"]


# ----- thread wrappers end-to-end (real thread, mocked MCP) -------------------

def test_fetch_idea_report_through_thread(monkeypatch):
    _install(monkeypatch, tool_names=["get_idea_report"],
             responses={"get_idea_report": _FakeResult(
                 content_text="# Stage 3\n> Corpus coverage: strong (5/9 hypotheses matched)\narxiv:1")})
    g = ag.fetch_idea_report("topic")
    assert g.ok and g.is_grounded and g.n_matched == 5


def test_fetch_stage3_bundle_through_thread(monkeypatch):
    _install(monkeypatch, tool_names=["research_e2e", "get_run_status"],
             responses={"research_e2e": _FakeResult(structured={
                 "status": "done", "idea_report_markdown": "arxiv:1 report",
                 "graph_html": "<g>", "coverage": {"n_matched": 3, "n_total": 9, "top_relevance": 2}})})
    b = ag.fetch_stage3_bundle("topic")
    assert b.ok and b.is_grounded and b.graph_html == "<g>" and b.n_matched == 3


def test_fetch_stage3_bundle_worker_exception_returns_error(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("mcp blew up")
    monkeypatch.setattr(ag, "_bundle_async", boom)
    b = ag.fetch_stage3_bundle("topic")
    assert b.ok is False and "RuntimeError" in b.error


def test_fetch_stage3_bundle_ideas_string_fallback(monkeypatch):
    async def fake(*a, **k):
        return "research_e2e", {"status": "done", "idea_report_markdown": "arxiv:1",
                                "ideas": "- idea as plain string"}
    monkeypatch.setattr(ag, "_bundle_async", fake)
    b = ag.fetch_stage3_bundle("topic")
    assert b.ok and b.ideas_markdown == "- idea as plain string"
