"""``write()`` publishes a ``file_written`` event the frontend turns
into a workspace tree row. ``file_path`` must be relative to the
caller's ``project_dir`` so the tree groups files under their stage
folder rather than under the absolute filesystem prefix
(``/Users/.../projects/<id>/iterations/<iter>/``).

Regression for: with the new folder-tree UI (PR #89) the live-write
events sent absolute paths, so each freshly written deliverable showed
up under nested ``/`` → ``Users`` → ``yuzhengxu`` → ... folders
instead of under ``stage8_paper``."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_file_written_event_uses_project_relative_path(tmp_path, monkeypatch):
    from onemancompany.agents.common_tools import write
    from onemancompany.core.events import event_bus

    project_dir = tmp_path / "iter_001"
    (project_dir / "stage8_paper").mkdir(parents=True)
    target = project_dir / "stage8_paper" / "main.tex"

    captured: dict = {}

    async def _capture(event):
        if (event.payload or {}).get("type") == "file_written":
            captured.update(event.payload)

    monkeypatch.setattr(event_bus, "publish", AsyncMock(side_effect=_capture))

    result = await write.ainvoke({
        "file_path": str(target),
        "content": "\\documentclass{article}",
        "employee_id": "00014",
        "project_dir": str(project_dir),
    })

    assert result["status"] == "ok"
    assert captured["file_path"] == "stage8_paper/main.tex", (
        "live-write event must send a project-relative path so the "
        "workspace tree groups files under their stage folder"
    )
    # ``full_path`` keeps the absolute disk path for the rare case the
    # frontend wants to surface it (it currently doesn't render it).
    assert captured["full_path"] == str(target)
    assert captured["file_name"] == "main.tex"


@pytest.mark.asyncio
async def test_file_written_falls_back_to_basename_outside_project_dir(tmp_path, monkeypatch):
    """If the agent writes outside the project tree (employee
    workspace, src/, etc.) we still mustn't leak the absolute path —
    fall back to the basename."""
    from onemancompany.agents.common_tools import write
    from onemancompany.core.events import event_bus

    project_dir = tmp_path / "proj-A"
    project_dir.mkdir()
    elsewhere = tmp_path / "elsewhere" / "note.md"
    elsewhere.parent.mkdir(parents=True)

    captured: dict = {}

    async def _capture(event):
        if (event.payload or {}).get("type") == "file_written":
            captured.update(event.payload)

    monkeypatch.setattr(event_bus, "publish", AsyncMock(side_effect=_capture))

    result = await write.ainvoke({
        "file_path": str(elsewhere),
        "content": "hello",
        "employee_id": "00014",
        "project_dir": str(project_dir),
    })

    assert result["status"] == "ok"
    assert captured["file_path"] == "note.md", (
        "absolute paths from outside project_dir must collapse to the "
        "basename — otherwise the workspace tree shows /Users/... folders"
    )


@pytest.mark.asyncio
async def test_file_written_basename_when_no_project_dir(tmp_path, monkeypatch):
    """Some call sites (one-on-one chat, EA inbox) invoke ``write``
    without a project context. We don't have a base to make the path
    relative to, so basename is the only safe choice."""
    from onemancompany.agents.common_tools import write
    from onemancompany.core.events import event_bus

    target = tmp_path / "ad_hoc_note.md"
    captured: dict = {}

    async def _capture(event):
        if (event.payload or {}).get("type") == "file_written":
            captured.update(event.payload)

    monkeypatch.setattr(event_bus, "publish", AsyncMock(side_effect=_capture))

    result = await write.ainvoke({
        "file_path": str(target),
        "content": "x",
        "employee_id": "00014",
        # project_dir intentionally omitted
    })

    assert result["status"] == "ok"
    assert captured["file_path"] == "ad_hoc_note.md"
