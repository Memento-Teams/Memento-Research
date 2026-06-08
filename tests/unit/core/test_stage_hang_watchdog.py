"""Coverage for ``stage_hang_watchdog`` (core/system_cron.py, added in #122).

The watchdog auto-recovers a pipeline stage whose agent hung: a node stuck
in PROCESSING whose ``debug_trace.jsonl`` has stopped advancing. It aborts
the project (routing through vessel -> engine re-dispatch) once per cooldown
window. These tests drive each decision branch with an in-memory TaskTree.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from onemancompany.core.task_lifecycle import TaskPhase
from onemancompany.core.task_tree import TaskTree


def _hung_tree(pid: str, project_dir: str, started_at: str, *, pipeline_managed=True):
    tree = TaskTree(project_id=pid)
    root = tree.create_root("00001", "root")
    node = tree.add_child(root.id, "00010", "stage node", [])
    node.set_status(TaskPhase.PROCESSING)
    node.started_at = started_at
    node.project_dir = project_dir
    node.metadata = {"pipeline_managed": True} if pipeline_managed else {}
    return tree


def _clear_cooldown():
    from onemancompany.core import system_cron
    system_cron._hang_aborted_at.clear()


@pytest.mark.asyncio
async def test_no_projects_dir_returns_none(tmp_path):
    _clear_cooldown()
    with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path / "missing"):
        from onemancompany.core.system_cron import stage_hang_watchdog
        assert await stage_hang_watchdog() is None


@pytest.mark.asyncio
async def test_hung_node_is_aborted(tmp_path):
    _clear_cooldown()
    two_hours_ago = (datetime.now() - timedelta(hours=2)).isoformat()
    # project_dir has NO debug_trace.jsonl => trace not fresh => confident hang
    proj = tmp_path / "wp"
    proj.mkdir()
    tree = _hung_tree("hung-proj", str(proj), two_hours_ago)
    em = MagicMock()
    (tmp_path / "task_tree.yaml").write_text("x", encoding="utf-8")
    with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path), \
         patch("onemancompany.core.config.TASK_TREE_FILENAME", "task_tree.yaml"), \
         patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
         patch("onemancompany.core.vessel.employee_manager", em):
        from onemancompany.core.system_cron import stage_hang_watchdog
        result = await stage_hang_watchdog()
    em.abort_project.assert_called_once_with("hung-proj")
    assert result is None  # watchdog always returns None


@pytest.mark.asyncio
async def test_fresh_trace_not_touched(tmp_path):
    _clear_cooldown()
    two_hours_ago = (datetime.now() - timedelta(hours=2)).isoformat()
    proj = tmp_path / "wp2"
    proj.mkdir()
    (proj / "debug_trace.jsonl").write_text("{}\n", encoding="utf-8")  # fresh mtime
    tree = _hung_tree("alive-proj", str(proj), two_hours_ago)
    em = MagicMock()
    (tmp_path / "task_tree.yaml").write_text("x", encoding="utf-8")
    with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path), \
         patch("onemancompany.core.config.TASK_TREE_FILENAME", "task_tree.yaml"), \
         patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
         patch("onemancompany.core.vessel.employee_manager", em):
        from onemancompany.core.system_cron import stage_hang_watchdog
        await stage_hang_watchdog()
    em.abort_project.assert_not_called()


@pytest.mark.asyncio
async def test_non_pipeline_managed_skipped(tmp_path):
    _clear_cooldown()
    two_hours_ago = (datetime.now() - timedelta(hours=2)).isoformat()
    proj = tmp_path / "wp3"
    proj.mkdir()
    tree = _hung_tree("ea-proj", str(proj), two_hours_ago, pipeline_managed=False)
    em = MagicMock()
    (tmp_path / "task_tree.yaml").write_text("x", encoding="utf-8")
    with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path), \
         patch("onemancompany.core.config.TASK_TREE_FILENAME", "task_tree.yaml"), \
         patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
         patch("onemancompany.core.vessel.employee_manager", em):
        from onemancompany.core.system_cron import stage_hang_watchdog
        await stage_hang_watchdog()
    em.abort_project.assert_not_called()


@pytest.mark.asyncio
async def test_unparsable_started_at_skipped(tmp_path):
    _clear_cooldown()
    proj = tmp_path / "wp4"
    proj.mkdir()
    tree = _hung_tree("bad-ts-proj", str(proj), "not-a-timestamp")
    em = MagicMock()
    (tmp_path / "task_tree.yaml").write_text("x", encoding="utf-8")
    with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path), \
         patch("onemancompany.core.config.TASK_TREE_FILENAME", "task_tree.yaml"), \
         patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
         patch("onemancompany.core.vessel.employee_manager", em):
        from onemancompany.core.system_cron import stage_hang_watchdog
        await stage_hang_watchdog()
    em.abort_project.assert_not_called()


@pytest.mark.asyncio
async def test_cooldown_blocks_reabort(tmp_path):
    _clear_cooldown()
    import time
    from onemancompany.core import system_cron
    two_hours_ago = (datetime.now() - timedelta(hours=2)).isoformat()
    proj = tmp_path / "wp5"
    proj.mkdir()
    tree = _hung_tree("cool-proj", str(proj), two_hours_ago)
    # mark just-aborted -> within COOLDOWN window
    system_cron._hang_aborted_at["cool-proj"] = time.time()
    em = MagicMock()
    (tmp_path / "task_tree.yaml").write_text("x", encoding="utf-8")
    with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path), \
         patch("onemancompany.core.config.TASK_TREE_FILENAME", "task_tree.yaml"), \
         patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
         patch("onemancompany.core.vessel.employee_manager", em):
        from onemancompany.core.system_cron import stage_hang_watchdog
        await stage_hang_watchdog()
    em.abort_project.assert_not_called()
    _clear_cooldown()


@pytest.mark.asyncio
async def test_abort_failure_is_logged_not_raised(tmp_path):
    _clear_cooldown()
    two_hours_ago = (datetime.now() - timedelta(hours=2)).isoformat()
    proj = tmp_path / "wp6"
    proj.mkdir()
    tree = _hung_tree("boom-proj", str(proj), two_hours_ago)
    em = MagicMock()
    em.abort_project.side_effect = RuntimeError("abort blew up")
    (tmp_path / "task_tree.yaml").write_text("x", encoding="utf-8")
    with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path), \
         patch("onemancompany.core.config.TASK_TREE_FILENAME", "task_tree.yaml"), \
         patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
         patch("onemancompany.core.vessel.employee_manager", em):
        from onemancompany.core.system_cron import stage_hang_watchdog
        # must not raise despite abort failing
        result = await stage_hang_watchdog()
    em.abort_project.assert_called_once()
    assert result is None


@pytest.mark.asyncio
async def test_unreadable_tree_skipped(tmp_path):
    _clear_cooldown()
    em = MagicMock()
    (tmp_path / "task_tree.yaml").write_text("x", encoding="utf-8")

    def _boom(_p):
        raise ValueError("corrupt tree")

    with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path), \
         patch("onemancompany.core.config.TASK_TREE_FILENAME", "task_tree.yaml"), \
         patch("onemancompany.core.task_tree.get_tree", side_effect=_boom), \
         patch("onemancompany.core.vessel.employee_manager", em):
        from onemancompany.core.system_cron import stage_hang_watchdog
        result = await stage_hang_watchdog()
    em.abort_project.assert_not_called()
    assert result is None
