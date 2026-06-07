"""R9-1: a locally-dead pipeline must not leak its remote runs.

When a pipeline fails (or is cancelled) while parked on
``pending_run_ids``, nothing watches those runs any more — they keep
executing on the infra for hours and eat the 4-slot concurrency quota.
Real incident: Round-7's pilot ``run_0569b7feb018`` ran 2h+ after its
pipeline died and (with unrelated neighbours) starved Round 9's
submissions into ``Concurrent run limit reached 4/4``.

``gc_orphan_runs`` walks terminal pipelines that still carry
``pending_run_ids``, cancels every run the infra listing does not show
terminal, persists the forensics, and clears the pending list so the GC
is one-shot per project."""
from __future__ import annotations

from pathlib import Path

import yaml

from onemancompany.core import run_tracker as rt


def _mk_project(root: Path, pid: str, state: dict) -> Path:
    iter_dir = root / pid / "iterations" / "iter_001"
    iter_dir.mkdir(parents=True)
    (iter_dir / "pipeline_state.yaml").write_text(
        yaml.safe_dump(state, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )
    return iter_dir / "pipeline_state.yaml"


def _read(state_file: Path) -> dict:
    return yaml.safe_load(state_file.read_text(encoding="utf-8"))


def test_gc_cancels_active_runs_of_failed_pipeline(tmp_path, monkeypatch):
    import onemancompany.core.config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path)

    cancelled = []
    monkeypatch.setattr(rt, "_cancel_infra_run", lambda rid: (cancelled.append(rid), True)[1])

    sf = _mk_project(tmp_path, "p_dead", {
        "current_stage": 6, "phase": "failed",
        "failure_reason": "stage_6_retries_exhausted",
        "pending_run_ids": ["run_orphan_a", "run_done_b"],
    })
    listing = [
        {"run_id": "run_orphan_a", "status": "running"},
        {"run_id": "run_done_b", "status": "succeeded"},
    ]

    n = rt.gc_orphan_runs(all_runs=listing)

    assert n == 1
    assert cancelled == ["run_orphan_a"], "only the still-active run is cancelled"
    st = _read(sf)
    assert st.get("pending_run_ids") in ([], None), "pending cleared → GC is one-shot"
    assert st.get("orphan_runs_cancelled") == ["run_orphan_a"], "forensics persisted"


def test_gc_cancels_runs_missing_from_listing(tmp_path, monkeypatch):
    """A pending run absent from the listing (pagination / lag) is treated
    as possibly-alive: cancel is attempted (the infra rejects cancels of
    already-terminal runs harmlessly)."""
    import onemancompany.core.config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path)
    cancelled = []
    monkeypatch.setattr(rt, "_cancel_infra_run", lambda rid: (cancelled.append(rid), True)[1])

    _mk_project(tmp_path, "p_dead2", {
        "current_stage": 6, "phase": "failed",
        "pending_run_ids": ["run_unlisted"],
    })

    n = rt.gc_orphan_runs(all_runs=[])

    assert n == 1
    assert cancelled == ["run_unlisted"]


def test_gc_leaves_waiting_pipeline_alone(tmp_path, monkeypatch):
    """A healthy parked pipeline keeps its pending runs — GC must only
    touch TERMINAL pipelines."""
    import onemancompany.core.config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path)
    cancelled = []
    monkeypatch.setattr(rt, "_cancel_infra_run", lambda rid: (cancelled.append(rid), True)[1])

    sf = _mk_project(tmp_path, "p_alive", {
        "current_stage": 6, "phase": "producer_b_waiting",
        "pending_run_ids": ["run_live"],
    })

    n = rt.gc_orphan_runs(all_runs=[{"run_id": "run_live", "status": "running"}])

    assert n == 0
    assert cancelled == []
    assert _read(sf).get("pending_run_ids") == ["run_live"]


def test_gc_no_candidates_skips_listing_fetch(tmp_path, monkeypatch):
    """With no terminal+pending projects, the GC must not even hit the
    infra listing (cron runs every 30s — keep the idle cost zero)."""
    import onemancompany.core.config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path)

    def _boom(*a, **k):
        raise AssertionError("listing must not be fetched with no candidates")
    monkeypatch.setattr(rt, "_list_infra_runs", _boom)

    _mk_project(tmp_path, "p_done", {
        "current_stage": 9, "phase": "done", "pending_run_ids": [],
    })

    assert rt.gc_orphan_runs() == 0
