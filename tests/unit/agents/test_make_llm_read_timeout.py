"""Regression: silent provider stalls (server keeps the socket open but
sends no chunks) must surface as ``httpx.ReadTimeout`` within a bounded
window so the existing L1 stream-retry (``run_streamed``) can catch and
retry instead of waiting for the 1-hour task-level timeout.

Reported: Stage 3 critic dispatched at 00:54:21 silently hung for 10+
minutes. No exception ever raised — ``request_timeout=300`` is for the
**total request budget**, not the inter-chunk read deadline, and
LangChain's openai client doesn't set a per-read httpx timeout by
default. So the only thing that would eventually catch the hang was the
3600 s outer task wait_for — by which point everyone has gone home.

Fix: build an ``httpx.AsyncClient`` with an explicit per-read timeout
and hand it to every ``ChatOpenAI`` constructor in ``make_llm``."""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest


# ---------------------------------------------------------------------------
# Module-level constants — locks the contract so future edits don't
# silently drop the read timeout (which is the whole point of this fix).
# ---------------------------------------------------------------------------

class TestReadTimeoutConstant:
    def test_constant_exists(self):
        from onemancompany.agents.base import _LLM_STREAM_READ_TIMEOUT
        assert _LLM_STREAM_READ_TIMEOUT > 0

    def test_constant_is_strictly_shorter_than_request_timeout(self):
        """If the per-chunk read timeout were ≥ the total request budget,
        it would never fire before the request budget does — turning the
        whole change into a no-op. Lock in the invariant: read budget
        must give multiple chunk windows inside one request budget."""
        from onemancompany.agents.base import _LLM_STREAM_READ_TIMEOUT
        # request_timeout in make_llm is 300.0 today; keep this lower
        # bound generous so a future bump to e.g. 600 doesn't break it.
        assert _LLM_STREAM_READ_TIMEOUT <= 120, (
            "read-side per-chunk timeout should be much shorter than the "
            "total request budget so silent stalls surface promptly"
        )


# ---------------------------------------------------------------------------
# ``make_llm`` wires a custom httpx async client with the read timeout.
# ---------------------------------------------------------------------------

class TestMakeLlmPassesHttpxClient:
    @pytest.fixture
    def _stub_settings(self, monkeypatch):
        """Minimal employee_configs + settings so make_llm reaches a
        ChatOpenAI constructor on the openrouter path."""
        from types import SimpleNamespace
        from onemancompany.agents import base
        from onemancompany.core import config as _cfg

        emp_cfg = SimpleNamespace(
            api_provider="openrouter",
            llm_model="anthropic/claude-sonnet-4",
            temperature=0.4,
            api_key="sk-test-employee",
            hosting="company",
            auth_method="api_key",
            oauth_refresh_token="",
        )
        monkeypatch.setattr(_cfg, "employee_configs", {"00007": emp_cfg}, raising=False)
        monkeypatch.setattr(_cfg.settings, "openrouter_api_key", "sk-test-global", raising=False)
        monkeypatch.setattr(_cfg.settings, "openrouter_base_url", "https://openrouter.example/v1", raising=False)
        return emp_cfg

    def test_chatopenai_receives_async_client_with_read_timeout(self, _stub_settings):
        """``make_llm`` must hand ``ChatOpenAI`` an ``http_async_client``
        whose timeout reflects ``_LLM_STREAM_READ_TIMEOUT``. Without this
        wiring, silent provider stalls never raise — they just wait."""
        from onemancompany.agents import base
        from onemancompany.agents.base import _LLM_STREAM_READ_TIMEOUT

        captured: dict = {}

        class _FakeChatOpenAI:
            def __init__(self, **kw):
                captured.update(kw)
                # Mirror BaseChatModel's interface enough for make_llm
                # to return us without further attribute lookups.

        with patch.object(base, "ChatOpenAI", _FakeChatOpenAI):
            base.make_llm(employee_id="00007")

        client = captured.get("http_async_client")
        assert client is not None, "ChatOpenAI must receive an http_async_client"
        # httpx.AsyncClient stores per-op deadlines on its .timeout attribute.
        assert isinstance(client.timeout, httpx.Timeout)
        assert client.timeout.read == _LLM_STREAM_READ_TIMEOUT, (
            f"read timeout {client.timeout.read} != configured "
            f"{_LLM_STREAM_READ_TIMEOUT}"
        )

    def test_read_timeout_present_on_fallback_path_too(self, monkeypatch):
        """``make_llm`` has an openrouter-fallback path used when an
        employee's configured provider has no key. The timeout wiring
        must be on BOTH constructors; locking the fallback path keeps a
        future refactor from regressing one of them."""
        from types import SimpleNamespace
        from onemancompany.agents import base
        from onemancompany.core import config as _cfg
        from onemancompany.agents.base import _LLM_STREAM_READ_TIMEOUT

        emp_cfg = SimpleNamespace(
            api_provider="anthropic",  # no key → falls back to openrouter
            llm_model="anthropic/claude-sonnet-4",
            temperature=0.4,
            api_key="",
            hosting="company",
            auth_method="api_key",
            oauth_refresh_token="",
        )
        monkeypatch.setattr(_cfg, "employee_configs", {"00007": emp_cfg}, raising=False)
        monkeypatch.setattr(_cfg.settings, "openrouter_api_key", "sk-test-global", raising=False)
        monkeypatch.setattr(_cfg.settings, "anthropic_api_key", "", raising=False)
        monkeypatch.setattr(_cfg.settings, "anthropic_oauth_token", "", raising=False)

        captured: dict = {}

        class _FakeChatOpenAI:
            def __init__(self, **kw):
                captured.update(kw)

        with patch.object(base, "ChatOpenAI", _FakeChatOpenAI):
            base.make_llm(employee_id="00007")

        client = captured.get("http_async_client")
        assert client is not None, "fallback path must also wire http_async_client"
        assert client.timeout.read == _LLM_STREAM_READ_TIMEOUT


# ---------------------------------------------------------------------------
# Sanity: httpx.ReadTimeout is already on the L1 transient list so the
# new TimeoutError from the read-side will actually be retried, not just
# converted to a different misleading message.
# ---------------------------------------------------------------------------

class TestReadTimeoutIsTransient:
    def test_httpx_read_timeout_classified_as_transient(self):
        """L1 stream retry only fires on transient errors. ``httpx.ReadTimeout``
        must be in that set or the new read-side timeout would just
        instantly fail without retrying."""
        from onemancompany.agents.base import _is_transient_network_error
        # Construct a ReadTimeout — its signature varies across httpx
        # versions; the message-string fallback in
        # _is_transient_network_error catches it regardless.
        try:
            exc = httpx.ReadTimeout("simulated idle-chunk timeout")
        except TypeError:
            exc = httpx.ReadTimeout("simulated idle-chunk timeout", request=None)
        assert _is_transient_network_error(exc) is True
