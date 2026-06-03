"""Unit tests for /api/infra/runs and /api/infra/budget routes.

Verifies that both routes:
- POST to the upstream infra server with ``{"session_key": ...}`` in the
  JSON body (the upstream contract — GET returns 404 on these paths).
- Return the upstream JSON unchanged on success.
- Return ``{"error": "INFRA not configured"}`` when env vars are missing.
- Return an ``{"error": ...}`` dict (not a 5xx) when the upstream call fails.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _make_test_app() -> FastAPI:
    from onemancompany.api.routes import router

    app = FastAPI()
    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# Helpers — build a fake httpx response
# ---------------------------------------------------------------------------


def _fake_httpx_response(payload: dict, status_code: int = 200):
    """Return a mock that looks like an httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()  # no-op: success
    return resp


# ---------------------------------------------------------------------------
# GET /api/infra/runs
# ---------------------------------------------------------------------------


class TestInfraRunsRoute:
    async def test_returns_upstream_json(self, monkeypatch):
        """Route returns the raw JSON from the upstream /api/list_runs."""
        monkeypatch.setenv("INFRA_SERVER_URL", "http://infra.example.com")
        monkeypatch.setenv("INFRA_SESSION_KEY", "test-key-abc")

        upstream_payload = {
            "runs": [
                {"run_id": "run-001", "status": "running", "cost": 0.05},
                {"run_id": "run-002", "status": "queued", "cost": 0.0},
            ]
        }
        fake_resp = _fake_httpx_response(upstream_payload)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=fake_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/infra/runs")

        assert resp.status_code == 200
        data = resp.json()
        assert data == upstream_payload

    async def test_forwards_session_key_in_body(self, monkeypatch):
        """Route POSTs the session key in the JSON body (upstream contract)."""
        monkeypatch.setenv("INFRA_SERVER_URL", "http://infra.example.com")
        monkeypatch.setenv("INFRA_SESSION_KEY", "secret-session-key")

        fake_resp = _fake_httpx_response({"runs": []})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=fake_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                await c.get("/api/infra/runs")

        body = mock_client.post.call_args.kwargs.get("json") or {}
        assert body.get("session_key") == "secret-session-key"

    async def test_calls_correct_upstream_path(self, monkeypatch):
        """Route calls /api/list_runs on the configured server URL.

        The configured URL intentionally has a trailing slash to exercise
        the route's ``.rstrip('/')`` normalization (otherwise the joined
        URL would be ``http://infra.example.com//api/list_runs``).
        """
        monkeypatch.setenv("INFRA_SERVER_URL", "http://infra.example.com/")
        monkeypatch.setenv("INFRA_SESSION_KEY", "k")

        fake_resp = _fake_httpx_response({"runs": []})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=fake_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                await c.get("/api/infra/runs")

        called_url = mock_client.post.call_args.args[0]
        assert called_url == "http://infra.example.com/api/list_runs"

    async def test_not_configured_when_env_missing(self, monkeypatch):
        """Returns {error: 'INFRA not configured'} when env vars are absent."""
        monkeypatch.delenv("INFRA_SERVER_URL", raising=False)
        monkeypatch.delenv("INFRA_SESSION_KEY", raising=False)

        app = _make_test_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/infra/runs")

        assert resp.status_code == 200
        assert resp.json() == {"error": "INFRA not configured"}

    async def test_not_configured_when_url_only(self, monkeypatch):
        """Returns error when only INFRA_SERVER_URL is set (key missing)."""
        monkeypatch.setenv("INFRA_SERVER_URL", "http://infra.example.com")
        monkeypatch.delenv("INFRA_SESSION_KEY", raising=False)

        app = _make_test_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/infra/runs")

        assert resp.status_code == 200
        assert resp.json() == {"error": "INFRA not configured"}

    async def test_upstream_error_returns_error_dict(self, monkeypatch):
        """Network/upstream errors are returned as {error: ...} rather than 5xx."""
        monkeypatch.setenv("INFRA_SERVER_URL", "http://infra.example.com")
        monkeypatch.setenv("INFRA_SESSION_KEY", "k")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/infra/runs")

        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
        assert data["error"] == "upstream request failed"


# ---------------------------------------------------------------------------
# GET /api/infra/budget
# ---------------------------------------------------------------------------


class TestInfraBudgetRoute:
    async def test_returns_upstream_json(self, monkeypatch):
        """Route returns the raw JSON from the upstream /api/budget."""
        monkeypatch.setenv("INFRA_SERVER_URL", "http://infra.example.com")
        monkeypatch.setenv("INFRA_SESSION_KEY", "test-key-abc")

        upstream_payload = {"used": 12.50, "total": 100.0}
        fake_resp = _fake_httpx_response(upstream_payload)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=fake_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/infra/budget")

        assert resp.status_code == 200
        assert resp.json() == upstream_payload

    async def test_forwards_session_key_in_body(self, monkeypatch):
        """Route POSTs the session key in the JSON body (upstream contract)."""
        monkeypatch.setenv("INFRA_SERVER_URL", "http://infra.example.com")
        monkeypatch.setenv("INFRA_SESSION_KEY", "budget-key-xyz")

        fake_resp = _fake_httpx_response({"used": 0, "total": 50})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=fake_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                await c.get("/api/infra/budget")

        body = mock_client.post.call_args.kwargs.get("json") or {}
        assert body.get("session_key") == "budget-key-xyz"

    async def test_calls_correct_upstream_path(self, monkeypatch):
        """Route calls /api/budget on the configured server URL."""
        monkeypatch.setenv("INFRA_SERVER_URL", "http://infra.example.com/")
        monkeypatch.setenv("INFRA_SESSION_KEY", "k")

        fake_resp = _fake_httpx_response({"used": 0})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=fake_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                await c.get("/api/infra/budget")

        called_url = mock_client.post.call_args.args[0]
        # Trailing slash should be stripped before appending path
        assert called_url == "http://infra.example.com/api/budget"

    async def test_not_configured_when_env_missing(self, monkeypatch):
        """Returns {error: 'INFRA not configured'} when env vars are absent."""
        monkeypatch.delenv("INFRA_SERVER_URL", raising=False)
        monkeypatch.delenv("INFRA_SESSION_KEY", raising=False)

        app = _make_test_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/infra/budget")

        assert resp.status_code == 200
        assert resp.json() == {"error": "INFRA not configured"}

    async def test_upstream_error_returns_error_dict(self, monkeypatch):
        """Network/upstream errors are returned as {error: ...} rather than 5xx."""
        monkeypatch.setenv("INFRA_SERVER_URL", "http://infra.example.com")
        monkeypatch.setenv("INFRA_SESSION_KEY", "k")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=Exception("timeout"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/infra/budget")

        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
        assert data["error"] == "upstream request failed"
