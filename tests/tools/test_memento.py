"""Unit tests for the memento asset tool.

The tool exposes two LangChain @tool functions: store and recall. These
tests use a fake Vessel set on the ContextVar to drive employee context,
and patch MemoryV4Adapter to avoid real LLM calls.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from onemancompany.core.vessel import _current_vessel


@pytest.fixture
def fake_vessel():
    return SimpleNamespace(employee_id="E00006")


@pytest.fixture
def employee_root(tmp_path, monkeypatch):
    employees_dir = tmp_path / "employees"
    employees_dir.mkdir()
    (employees_dir / "E00006").mkdir()
    monkeypatch.setattr(
        "onemancompany.core.config.EMPLOYEES_DIR", employees_dir, raising=False
    )
    import company.assets.tools.memento.memento as memento_mod
    monkeypatch.setattr(memento_mod, "EMPLOYEES_DIR", employees_dir, raising=False)
    return employees_dir


@contextmanager
def _with_vessel(fake_vessel):
    token = _current_vessel.set(fake_vessel)
    try:
        yield
    finally:
        _current_vessel.reset(token)


def test_store_requires_employee_context(employee_root):
    from company.assets.tools.memento.memento import store

    result = store.invoke({"turns": [{"role": "user", "content": "hi"}]})

    assert result["status"] == "error"
    assert "employee context" in result["message"].lower()


def test_recall_requires_employee_context(employee_root):
    from company.assets.tools.memento.memento import recall

    result = recall.invoke({"query": "anything"})

    assert result["status"] == "error"
    assert "employee context" in result["message"].lower()


def test_store_rejects_empty_turns(employee_root, fake_vessel):
    from company.assets.tools.memento.memento import store

    with _with_vessel(fake_vessel):
        result = store.invoke({"turns": []})

    assert result["status"] == "error"
    assert "non-empty" in result["message"].lower()


def test_store_rejects_non_list_turns(employee_root, fake_vessel):
    from company.assets.tools.memento.memento import store

    with _with_vessel(fake_vessel):
        # LangChain @tool runs Pydantic validation that may reject a string
        # before our handler — accept either a tool error or our own error.
        try:
            result = store.invoke({"turns": "not a list"})
        except Exception as exc:
            assert "list" in str(exc).lower() or "valid" in str(exc).lower()
            return

    assert result["status"] == "error"
    assert "list" in result["message"].lower()


def test_store_rejects_turn_missing_role(employee_root, fake_vessel):
    from company.assets.tools.memento.memento import store

    with _with_vessel(fake_vessel):
        result = store.invoke({"turns": [{"content": "hi"}]})

    assert result["status"] == "error"
    assert "role" in result["message"].lower()


def test_store_rejects_invalid_role(employee_root, fake_vessel):
    from company.assets.tools.memento.memento import store

    with _with_vessel(fake_vessel):
        result = store.invoke({
            "turns": [{"role": "system", "content": "hi"}]
        })

    assert result["status"] == "error"
    assert "invalid role" in result["message"].lower() or "role" in result["message"].lower()


def test_store_happy_path_patches_adapter(employee_root, fake_vessel, monkeypatch):
    """store writes the session JSON, then ingests via the adapter."""
    from company.assets.tools.memento import memento as memento_mod

    captured = {}

    class _FakeAdapter:
        def __init__(self, **kwargs):
            captured["init_kwargs"] = kwargs
            self._memory_root = kwargs["memory_root"]

        async def setup(self):
            captured["setup_called"] = True

        async def ingest(self, conv, conv_id):
            captured["ingest_conv_id"] = conv_id
            captured["ingest_session_count"] = len(conv.sessions)

    monkeypatch.setattr(memento_mod, "MemoryV4Adapter", _FakeAdapter)

    with _with_vessel(fake_vessel):
        result = memento_mod.store.invoke({
            "turns": [
                {"role": "user", "content": "find auth bug"},
                {"role": "assistant", "content": "reproduced AUTH-742"},
            ]
        })

    assert result["status"] == "ok", result
    assert result["session_num"] == 1
    assert result["session_id"].endswith("_sess1")

    sessions_dir = employee_root / "E00006" / "memory" / "sessions"
    written = sorted(sessions_dir.glob("*.json"))
    assert len(written) == 1
    payload = json.loads(written[0].read_text())
    assert payload["session_num"] == 1
    assert len(payload["turns"]) == 2
    assert payload["turns"][0]["content"] == "find auth bug"

    assert captured["ingest_conv_id"] == "E00006"
    assert captured["ingest_session_count"] == 1


def test_store_increments_session_num(employee_root, fake_vessel, monkeypatch):
    """Three consecutive stores produce session_nums 1, 2, 3 with 001/002/003.json."""
    from company.assets.tools.memento import memento as memento_mod

    class _NoopAdapter:
        def __init__(self, **_):
            pass

        async def setup(self):
            pass

        async def ingest(self, *_a, **_kw):
            pass

    monkeypatch.setattr(memento_mod, "MemoryV4Adapter", _NoopAdapter)

    with _with_vessel(fake_vessel):
        r1 = memento_mod.store.invoke({"turns": [{"role": "user", "content": "task one"}]})
        r2 = memento_mod.store.invoke({"turns": [{"role": "user", "content": "task two"}]})
        r3 = memento_mod.store.invoke({"turns": [{"role": "user", "content": "task three"}]})

    assert r1["session_num"] == 1
    assert r2["session_num"] == 2
    assert r3["session_num"] == 3

    sessions_dir = employee_root / "E00006" / "memory" / "sessions"
    written = sorted(p.name for p in sessions_dir.glob("*.json"))
    assert written == ["001.json", "002.json", "003.json"]
