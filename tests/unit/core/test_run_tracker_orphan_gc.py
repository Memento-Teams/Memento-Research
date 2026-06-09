"""run_tracker regression tests (R13-2).

The orphan-GC suite for R9-1 lives on the #126 branch; this file carries
the R13-2 waiting-transition regression independently so it runs on main.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

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


def test_waiting_transition_fires_even_when_run_map_unchanged(tmp_path, monkeypatch):
    """REGRESSION (run df3fd56612e5): the project's run was ALREADY terminal
    when it parked, so the tracker's per-project map never changed after the
    first poll — and the old 'no change, skip' early-continue sat ABOVE the
    producer_b_waiting branch, starving the transition forever (5.5h parked
    on a succeeded run; only the 12h deadline would have ended it)."""
    import onemancompany.core.config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path)

    run_record = {
        "run_id": "run_done",
        "status": "succeeded",
        "run_command": "cd omc/p_parked/iter_001 && python experiment.py",
    }
    monkeypatch.setattr(rt, "_list_infra_runs", lambda limit=100: [run_record])

    # State on disk: parked, pending on that run, and the run map ALREADY
    # equal to what this tick will compute (no change).
    _mk_project(tmp_path, "p_parked", {
        "current_stage": 6,
        "phase": "producer_b_waiting",
        "pending_run_ids": ["run_done"],
        "stage_6_runs": {"run_done": rt._summarise_run(run_record)},
    })

    eng = MagicMock()
    from onemancompany.core import pipeline_engine as pe
    monkeypatch.setattr(pe, "get_or_load_pipeline", lambda pid, pdir: eng)
    monkeypatch.setitem(pe._active_pipelines, "p_parked", eng)

    asyncio.run(rt.poll_active_projects())

    eng.on_runs_all_terminal.assert_called_once()
