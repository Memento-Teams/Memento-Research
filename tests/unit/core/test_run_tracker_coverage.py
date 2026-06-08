"""Coverage for the parts of ``core/run_tracker.py`` added with the
Stage-6 long-running waiter + orphan-GC work (#93/#97/#126): the infra
HTTP helpers, the disk-walk pollers, and the ``producer_b_waiting``
transition driven from disk state.

These exercise pure/IO-light branches with a tmp ``PROJECTS_DIR`` and a
monkeypatched ``httpx`` so no network is touched.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from onemancompany.core import run_tracker as rt


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, payload, raise_exc=None):
        self._payload = payload
        self._raise = raise_exc

    def raise_for_status(self):
        if self._raise is not None:
            raise self._raise

    def json(self):
        return self._payload


def _mk_state(root: Path, pid: str, state: dict, iter_id: str = "iter_001") -> Path:
    iter_dir = root / pid / "iterations" / iter_id
    iter_dir.mkdir(parents=True)
    sf = iter_dir / "pipeline_state.yaml"
    sf.write_text(yaml.safe_dump(state, default_flow_style=False, allow_unicode=True), encoding="utf-8")
    return sf


# ---------------------------------------------------------------------------
# _list_infra_runs
# ---------------------------------------------------------------------------

def test_list_infra_runs_no_creds_returns_empty(monkeypatch):
    monkeypatch.delenv("INFRA_SERVER_URL", raising=False)
    monkeypatch.delenv("INFRA_SESSION_KEY", raising=False)
    assert rt._list_infra_runs() == []


def test_list_infra_runs_success(monkeypatch):
    monkeypatch.setenv("INFRA_SERVER_URL", "http://infra.test/")
    monkeypatch.setenv("INFRA_SESSION_KEY", "k")
    runs = [{"run_id": "r1", "status": "running"}]
    monkeypatch.setattr(rt.httpx, "post", lambda *a, **k: _FakeResp({"runs": runs}))
    assert rt._list_infra_runs(limit=5) == runs


def test_list_infra_runs_non_list_payload(monkeypatch):
    monkeypatch.setenv("INFRA_SERVER_URL", "http://infra.test")
    monkeypatch.setenv("INFRA_SESSION_KEY", "k")
    monkeypatch.setattr(rt.httpx, "post", lambda *a, **k: _FakeResp({"runs": "nope"}))
    assert rt._list_infra_runs() == []


def test_list_infra_runs_http_error(monkeypatch):
    monkeypatch.setenv("INFRA_SERVER_URL", "http://infra.test")
    monkeypatch.setenv("INFRA_SESSION_KEY", "k")

    def _boom(*a, **k):
        raise rt.httpx.HTTPError("down")

    monkeypatch.setattr(rt.httpx, "post", _boom)
    assert rt._list_infra_runs() == []


# ---------------------------------------------------------------------------
# _cancel_infra_run
# ---------------------------------------------------------------------------

def test_cancel_infra_run_no_creds(monkeypatch):
    monkeypatch.delenv("INFRA_SERVER_URL", raising=False)
    monkeypatch.delenv("INFRA_SESSION_KEY", raising=False)
    assert rt._cancel_infra_run("r1") is False


def test_cancel_infra_run_success(monkeypatch):
    monkeypatch.setenv("INFRA_SERVER_URL", "http://infra.test")
    monkeypatch.setenv("INFRA_SESSION_KEY", "k")
    monkeypatch.setattr(rt.httpx, "post", lambda *a, **k: _FakeResp({"ok": True}))
    assert rt._cancel_infra_run("r1") is True


def test_cancel_infra_run_error(monkeypatch):
    monkeypatch.setenv("INFRA_SERVER_URL", "http://infra.test")
    monkeypatch.setenv("INFRA_SESSION_KEY", "k")

    def _boom(*a, **k):
        raise rt.httpx.HTTPError("nope")

    monkeypatch.setattr(rt.httpx, "post", _boom)
    assert rt._cancel_infra_run("r1") is False


# ---------------------------------------------------------------------------
# gc_orphan_runs — edge branches
# ---------------------------------------------------------------------------

def test_gc_no_projects_dir(monkeypatch, tmp_path):
    import onemancompany.core.config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path / "missing")
    assert rt.gc_orphan_runs() == 0


def test_gc_self_fetches_listing_when_none(monkeypatch, tmp_path):
    """all_runs=None must trigger _list_infra_runs (only when candidates exist)."""
    import onemancompany.core.config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path)
    _mk_state(tmp_path, "p1", {"phase": "failed", "pending_run_ids": ["rX"]})
    called = {"n": 0}

    def _fake_list():
        called["n"] += 1
        return [{"run_id": "rX", "status": "running"}]

    monkeypatch.setattr(rt, "_list_infra_runs", _fake_list)
    monkeypatch.setattr(rt, "_cancel_infra_run", lambda rid: True)
    assert rt.gc_orphan_runs() == 1
    assert called["n"] == 1


def test_gc_skips_unreadable_and_nondict_states(monkeypatch, tmp_path):
    import onemancompany.core.config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path)
    # underscore/system dir is skipped entirely
    (tmp_path / "_sys").mkdir()
    # a project dir with no iterations dir
    (tmp_path / "no_iters").mkdir()
    # corrupt YAML
    bad = tmp_path / "pbad" / "iterations" / "iter_001"
    bad.mkdir(parents=True)
    (bad / "pipeline_state.yaml").write_text("{not: valid: yaml: ::", encoding="utf-8")
    # non-dict YAML (a bare list)
    nd = tmp_path / "pnd" / "iterations" / "iter_001"
    nd.mkdir(parents=True)
    (nd / "pipeline_state.yaml").write_text("- just\n- a list\n", encoding="utf-8")
    # no candidates => listing never fetched
    monkeypatch.setattr(rt, "_list_infra_runs", lambda: (_ for _ in ()).throw(AssertionError("no fetch")))
    assert rt.gc_orphan_runs() == 0


def test_gc_persist_oserror_is_swallowed(monkeypatch, tmp_path):
    import onemancompany.core.config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path)
    _mk_state(tmp_path, "p1", {"phase": "failed", "pending_run_ids": ["rX"]})
    monkeypatch.setattr(rt, "_cancel_infra_run", lambda rid: True)

    real_write = Path.write_text

    def _maybe_boom(self, *a, **k):
        if self.name == "pipeline_state.yaml":
            raise OSError("disk full")
        return real_write(self, *a, **k)

    monkeypatch.setattr(Path, "write_text", _maybe_boom)
    # cancel still counted even though persistence failed
    assert rt.gc_orphan_runs(all_runs=[{"run_id": "rX", "status": "running"}]) == 1


# ---------------------------------------------------------------------------
# _should_poll_state
# ---------------------------------------------------------------------------

def test_should_poll_state_branches():
    assert rt._should_poll_state({"current_stage": 5}) is False
    assert rt._should_poll_state({"current_stage": 6, "phase": "idea"}) is False
    assert rt._should_poll_state({"current_stage": 6, "phase": "producer_b_waiting"}) is True
    # done + no timestamp => poll
    assert rt._should_poll_state({"current_stage": 6, "phase": "done"}) is True
    # done + unparsable timestamp => poll
    assert rt._should_poll_state(
        {"current_stage": 6, "phase": "done", "stage_started_at": {"6": "not-a-date"}}
    ) is True
    # done + fresh timestamp => poll
    fresh = datetime.now(timezone.utc).isoformat()
    assert rt._should_poll_state(
        {"current_stage": 6, "phase": "done", "stage_started_at": {"6": fresh}}
    ) is True
    # done + stale (older than the 6h window) => skip
    old = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    assert rt._should_poll_state(
        {"current_stage": 6, "phase": "done", "stage_started_at": {"6": old}}
    ) is False


# ---------------------------------------------------------------------------
# _iter_active_project_iter_dirs
# ---------------------------------------------------------------------------

def test_iter_active_no_projects_dir(monkeypatch, tmp_path):
    import onemancompany.core.config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path / "missing")
    assert rt._iter_active_project_iter_dirs() == []


def test_iter_active_filters_and_includes(monkeypatch, tmp_path):
    import onemancompany.core.config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path)
    # a stray file (not a dir) at the top level
    (tmp_path / "stray.txt").write_text("x", encoding="utf-8")
    # underscore dir skipped
    (tmp_path / "_adhoc").mkdir()
    # project with no iterations dir
    (tmp_path / "no_iters").mkdir()
    # project with iter but no state file
    (tmp_path / "no_state" / "iterations" / "iter_001").mkdir(parents=True)
    # corrupt yaml
    bad = tmp_path / "pbad" / "iterations" / "iter_001"
    bad.mkdir(parents=True)
    (bad / "pipeline_state.yaml").write_text("a: : :", encoding="utf-8")
    # non-dict yaml
    nd = tmp_path / "pnd" / "iterations" / "iter_001"
    nd.mkdir(parents=True)
    (nd / "pipeline_state.yaml").write_text("- 1\n- 2\n", encoding="utf-8")
    # an inactive project (not stage 6)
    _mk_state(tmp_path, "pinactive", {"current_stage": 3, "phase": "idea"})
    # a genuinely active one
    _mk_state(tmp_path, "pactive", {"current_stage": 6, "phase": "producer_b"})

    out = rt._iter_active_project_iter_dirs()
    pids = {pid for pid, _it, _p in out}
    assert pids == {"pactive"}


# ---------------------------------------------------------------------------
# poll_active_projects  (async)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_poll_no_targets_runs_gc(monkeypatch, tmp_path):
    import onemancompany.core.config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path)
    gc_called = {"n": 0}
    monkeypatch.setattr(rt, "gc_orphan_runs", lambda *a, **k: gc_called.__setitem__("n", gc_called["n"] + 1))
    out = await rt.poll_active_projects()
    assert out == {}
    assert gc_called["n"] == 1


@pytest.mark.asyncio
async def test_poll_targets_but_no_runs(monkeypatch, tmp_path):
    import onemancompany.core.config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path)
    _mk_state(tmp_path, "pactive", {"current_stage": 6, "phase": "producer_b"})
    monkeypatch.setattr(rt, "_list_infra_runs", lambda *a, **k: [])
    monkeypatch.setattr(rt, "gc_orphan_runs", lambda *a, **k: 0)
    out = await rt.poll_active_projects()
    assert out == {"pactive": 0}


@pytest.mark.asyncio
async def test_poll_updates_state_and_counts(monkeypatch, tmp_path):
    import onemancompany.core.config as cfg
    import onemancompany.core.pipeline_engine as pe
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path)
    sf = _mk_state(tmp_path, "pid1", {"current_stage": 6, "phase": "producer_b"})
    runs = [{"run_id": "rA", "status": "running",
             "run_command": "cd omc/pid1/iter_001/up && python x.py"}]
    monkeypatch.setattr(rt, "_list_infra_runs", lambda *a, **k: runs)
    monkeypatch.setattr(rt, "gc_orphan_runs", lambda *a, **k: 0)
    monkeypatch.setattr(pe, "_active_pipelines", {}, raising=False)
    out = await rt.poll_active_projects()
    assert out == {"pid1": 1}
    st = yaml.safe_load(sf.read_text(encoding="utf-8"))
    assert "rA" in st["stage_6_runs"]


@pytest.mark.asyncio
async def test_poll_producer_b_waiting_all_terminal_finalizes(monkeypatch, tmp_path):
    import onemancompany.core.config as cfg
    import onemancompany.core.pipeline_engine as pe
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path)
    _mk_state(tmp_path, "pid2", {
        "current_stage": 6, "phase": "producer_b_waiting",
        "pending_run_ids": ["rT"],
    })
    runs = [{"run_id": "rT", "status": "succeeded",
             "run_command": "cd omc/pid2/iter_001/up && python x.py"}]
    monkeypatch.setattr(rt, "_list_infra_runs", lambda *a, **k: runs)
    monkeypatch.setattr(rt, "gc_orphan_runs", lambda *a, **k: 0)

    class _Eng:
        def __init__(self):
            self.state = {}
            self.finalized = False
        def on_runs_all_terminal(self):
            self.finalized = True
        def on_runs_wait_timeout(self, n):  # pragma: no cover - not expected here
            raise AssertionError("should not time out")

    eng = _Eng()
    monkeypatch.setattr(pe, "_active_pipelines", {"pid2": eng}, raising=False)
    await rt.poll_active_projects()
    assert eng.finalized is True


@pytest.mark.asyncio
async def test_poll_producer_b_waiting_deadline_times_out(monkeypatch, tmp_path):
    import onemancompany.core.config as cfg
    import onemancompany.core.pipeline_engine as pe
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path)
    long_ago = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
    _mk_state(tmp_path, "pid3", {
        "current_stage": 6, "phase": "producer_b_waiting",
        "pending_run_ids": ["rStuck"],
        "pending_waiting_started_at": long_ago,
    })
    runs = [{"run_id": "rStuck", "status": "running",
             "run_command": "cd omc/pid3/iter_001/up && python x.py"}]
    monkeypatch.setattr(rt, "_list_infra_runs", lambda *a, **k: runs)
    monkeypatch.setattr(rt, "gc_orphan_runs", lambda *a, **k: 0)

    class _Eng:
        def __init__(self):
            self.state = {}
            self.timed_out = None
        def on_runs_all_terminal(self):  # pragma: no cover - not expected here
            raise AssertionError("not all terminal")
        def on_runs_wait_timeout(self, n):
            self.timed_out = n

    eng = _Eng()
    monkeypatch.setattr(pe, "_active_pipelines", {"pid3": eng}, raising=False)
    await rt.poll_active_projects()
    assert eng.timed_out is not None and eng.timed_out >= 12 * 60 * 60
