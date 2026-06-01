"""Behavioural tests for the advisory stage-eval event consumer.

The consumer subscribes to ``stage_complete`` STATE_SNAPSHOT events and
dispatches a dedicated eval-agent to write an advisory report. It must:
filter to pipeline events, dedup rapid re-fires, no-op cleanly when no
eval-agent is hired, and never raise into the event loop."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from onemancompany.core import eval_triggers as et
from onemancompany.core.models import EventType


@pytest.fixture(autouse=True)
def clear_dedup():
    et._recent_dispatch.clear()
    yield
    et._recent_dispatch.clear()


def _cfg(skills: list[str]) -> SimpleNamespace:
    return SimpleNamespace(skills=skills)


def _stage_event(**overrides) -> SimpleNamespace:
    payload = {
        "type": "stage_complete",
        "stage": 2,
        "stage_name": "Literature Survey",
        "project_id": "proj-1",
        "project_dir": "/tmp/proj-1/iter_001",
        "pipeline_managed": True,
    }
    payload.update(overrides)
    return SimpleNamespace(type=EventType.STATE_SNAPSHOT, payload=payload)


# --- _find_eval_employee -------------------------------------------------

def test_find_eval_employee_matches_skill(monkeypatch):
    monkeypatch.setattr(
        et,
        "load_employee_configs",
        lambda: {"00010": _cfg(["paper_writer"]), "00011": _cfg(["stage_eval"])},
    )
    assert et._find_eval_employee() == "00011"


def test_find_eval_employee_none_when_absent(monkeypatch):
    monkeypatch.setattr(
        et, "load_employee_configs", lambda: {"00010": _cfg(["paper_writer"])}
    )
    assert et._find_eval_employee() is None


# --- handle_stage_complete ----------------------------------------------

async def test_dispatches_on_valid_stage_complete(monkeypatch):
    calls = []
    monkeypatch.setattr(
        et,
        "_dispatch_eval_for_stage",
        lambda pid, pdir, sid, sname: calls.append((pid, pdir, sid, sname)) or "node-1",
    )
    dispatched = await et.handle_stage_complete(_stage_event())
    assert dispatched is True
    assert calls == [("proj-1", "/tmp/proj-1/iter_001", 2, "Literature Survey")]


async def test_ignores_non_pipeline_event(monkeypatch):
    calls = []
    monkeypatch.setattr(
        et, "_dispatch_eval_for_stage", lambda *a: calls.append(a) or "node-1"
    )
    ev = _stage_event(pipeline_managed=False)
    assert await et.handle_stage_complete(ev) is False
    assert calls == []


async def test_ignores_missing_fields(monkeypatch):
    calls = []
    monkeypatch.setattr(
        et, "_dispatch_eval_for_stage", lambda *a: calls.append(a) or "node-1"
    )
    ev = _stage_event()
    ev.payload.pop("project_dir")
    assert await et.handle_stage_complete(ev) is False
    assert calls == []


async def test_dedups_rapid_refire(monkeypatch):
    calls = []
    monkeypatch.setattr(
        et, "_dispatch_eval_for_stage", lambda *a: calls.append(a) or "node-1"
    )
    assert await et.handle_stage_complete(_stage_event()) is True
    # Same (project, stage) again within the dedup window → skipped.
    assert await et.handle_stage_complete(_stage_event()) is False
    assert len(calls) == 1
    # A different stage is still dispatched.
    assert await et.handle_stage_complete(_stage_event(stage=6)) is True
    assert len(calls) == 2


async def test_no_dedup_recorded_when_dispatch_returns_none(monkeypatch):
    """If no eval-agent is hired, dispatch returns None and we must NOT mark
    the stage as done — a later attempt (after hiring) should retry."""
    results = iter([None, "node-1"])
    calls = []

    def fake_dispatch(*a):
        calls.append(a)
        return next(results)

    monkeypatch.setattr(et, "_dispatch_eval_for_stage", fake_dispatch)
    assert await et.handle_stage_complete(_stage_event()) is False
    assert await et.handle_stage_complete(_stage_event()) is True
    assert len(calls) == 2


def test_dispatch_returns_none_without_eval_employee(monkeypatch):
    monkeypatch.setattr(et, "_find_eval_employee", lambda: None)
    assert et._dispatch_eval_for_stage("p", "/tmp/p", 2, "Literature Survey") is None


# --- register_eval_triggers ---------------------------------------------

def test_register_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(et.settings, "stage_eval_enabled", False)
    assert et.register_eval_triggers() is None


async def test_register_returns_task_when_enabled(monkeypatch):
    monkeypatch.setattr(et.settings, "stage_eval_enabled", True)
    task = et.register_eval_triggers()
    try:
        assert task is not None
    finally:
        task.cancel()
