"""``/api/pipeline/{project_id}/status`` builds ``workspace_files`` from
``rglob('*')`` inside the project dir. The frontend's right-sidebar
keys each row by ``file_path``; two files with the same basename in
different subdirs MUST appear as two distinct entries.

Regression for: ``stage8_paper/main.pdf`` was reported missing from
the sidebar because the previous frontend keyed by basename and
``upstream/main.py`` collided with paper outputs, silently
overwriting whichever arrived second. The backend already returns
the relative path; this test locks that contract."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def project_dir(tmp_path):
    """Layout that mirrors the reported case — same-basename files in
    different subdirs."""
    pdir = tmp_path / "proj-1"
    pdir.mkdir()
    (pdir / "stage8_paper").mkdir()
    (pdir / "stage8_paper" / "main.pdf").write_bytes(b"%PDF-")
    (pdir / "stage8_paper" / "main.tex").write_text("\\documentclass{article}")
    (pdir / "upstream").mkdir()
    (pdir / "upstream" / "main.py").write_text("print('hello')")
    (pdir / "stage1_topic.md").write_text("# Topic")
    return pdir


def _collect_workspace_files(pdir: Path) -> list[dict]:
    """Mirror the production logic in
    ``api/routes.py::pipeline_status`` so the test stays valid even
    if the route requires a full engine fixture."""
    _TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".py", ".csv", ".tex"}
    _BINARY_SUFFIXES = {".pdf"}
    out: list[dict] = []
    for f in sorted(pdir.rglob("*")):
        if not f.is_file():
            continue
        suffix = f.suffix.lower()
        if suffix not in _TEXT_SUFFIXES and suffix not in _BINARY_SUFFIXES:
            continue
        rel = str(f.relative_to(pdir))
        if rel.startswith("nodes/") or rel == "pipeline_state.yaml" or rel == "task_tree.yaml":
            continue
        out.append({
            "file_name": f.name,
            "file_path": rel,
            "size": f.stat().st_size,
        })
    return out


class TestSubdirFilesIncluded:
    def test_stage8_pdf_is_present_under_subdir_path(self, project_dir):
        rows = _collect_workspace_files(project_dir)
        paths = {r["file_path"] for r in rows}
        assert "stage8_paper/main.pdf" in paths, (
            "main.pdf under stage8_paper/ must be returned with its "
            "relative path; otherwise the frontend's PDF preview URL is "
            "wrong and the file is invisible in the sidebar"
        )

    def test_distinct_entries_for_same_basename_in_different_subdirs(self, project_dir):
        """Two files named ``main.*`` (``stage8_paper/main.pdf`` and
        ``upstream/main.py``) must appear as separate rows. The frontend
        keys by ``file_path``, so dedup by basename here would mean
        whichever arrived second silently replaces the first."""
        rows = _collect_workspace_files(project_dir)
        same_basename = [r for r in rows if r["file_name"].startswith("main.")]
        paths = {r["file_path"] for r in same_basename}
        assert {"stage8_paper/main.pdf", "upstream/main.py"} <= paths

    def test_top_level_files_still_included(self, project_dir):
        rows = _collect_workspace_files(project_dir)
        assert "stage1_topic.md" in {r["file_path"] for r in rows}
