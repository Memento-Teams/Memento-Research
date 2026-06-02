"""Unit tests for the cspaper_review asset tool.

The tool submits a paper PDF to cspaper.org and polls for the review. With no
CSPAPER_API_KEY it must return ``disabled`` so the Stage Eval Agent falls back
to the bundled review_template_en.md. All network calls are mocked."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

_MOD = "company.assets.tools.cspaper_review.cspaper_review"


@pytest.fixture
def pdf(tmp_path):
    p = tmp_path / "paper.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    return str(p)


@pytest.fixture(autouse=True)
def _no_settings_key(monkeypatch):
    """Force the key to come only from env so tests are deterministic
    regardless of any local .env."""
    monkeypatch.setattr(f"{_MOD}._setting", _setting_from_env)


def _setting_from_env(attr, env, default=""):
    import os
    return os.environ.get(env, default)


def _resp(payload):
    m = MagicMock()
    m.json.return_value = payload
    return m


def test_disabled_without_key(monkeypatch, pdf):
    monkeypatch.delenv("CSPAPER_API_KEY", raising=False)
    from company.assets.tools.cspaper_review.cspaper_review import cspaper_review

    out = cspaper_review.invoke({"file_path": pdf})
    assert out["status"] == "disabled"
    assert "review_template_en.md" in out["message"]


def test_missing_pdf_returns_error(monkeypatch, tmp_path):
    monkeypatch.setenv("CSPAPER_API_KEY", "csp_live_test")
    from company.assets.tools.cspaper_review.cspaper_review import cspaper_review

    out = cspaper_review.invoke({"file_path": str(tmp_path / "nope.pdf")})
    assert out["status"] == "error"
    assert "not found" in out["message"]


def test_submit_and_poll_ok(monkeypatch, pdf):
    monkeypatch.setenv("CSPAPER_API_KEY", "csp_live_test")
    from company.assets.tools.cspaper_review.cspaper_review import cspaper_review

    fake_httpx = MagicMock()
    fake_httpx.post.return_value = _resp({"data": {"job_id": "j1", "status": "PENDING"}})
    fake_httpx.get.return_value = _resp({"status": "COMPLETED", "data": {"review": "great"}})

    with patch(f"{_MOD}.httpx", fake_httpx):
        out = cspaper_review.invoke({"file_path": pdf, "agent_id": "NeurIPS_main_2025_1"})

    assert out["status"] == "ok"
    assert out["job_id"] == "j1"
    assert out["review"]["data"]["review"] == "great"


def test_no_job_id_is_error(monkeypatch, pdf):
    monkeypatch.setenv("CSPAPER_API_KEY", "csp_live_test")
    from company.assets.tools.cspaper_review.cspaper_review import cspaper_review

    fake_httpx = MagicMock()
    fake_httpx.post.return_value = _resp({"data": {}})
    with patch(f"{_MOD}.httpx", fake_httpx):
        out = cspaper_review.invoke({"file_path": pdf})
    assert out["status"] == "error"
    assert "job_id" in out["message"]


def test_terminal_failed_status(monkeypatch, pdf):
    monkeypatch.setenv("CSPAPER_API_KEY", "csp_live_test")
    from company.assets.tools.cspaper_review.cspaper_review import cspaper_review

    fake_httpx = MagicMock()
    fake_httpx.post.return_value = _resp({"data": {"job_id": "j2", "status": "PENDING"}})
    fake_httpx.get.return_value = _resp({"status": "FAILED"})
    with patch(f"{_MOD}.httpx", fake_httpx):
        out = cspaper_review.invoke({"file_path": pdf})
    assert out["status"] == "failed"
    assert out["job_id"] == "j2"


def test_submit_exception_is_error(monkeypatch, pdf):
    monkeypatch.setenv("CSPAPER_API_KEY", "csp_live_test")
    from company.assets.tools.cspaper_review.cspaper_review import cspaper_review

    fake_httpx = MagicMock()
    fake_httpx.post.side_effect = RuntimeError("boom")
    with patch(f"{_MOD}.httpx", fake_httpx):
        out = cspaper_review.invoke({"file_path": pdf})
    assert out["status"] == "error"
    assert "boom" in out["message"]
