"""cspaper.org "Agentic Review" tool.

Submit a paper PDF to cspaper.org and return a conference-style peer review.
This is an *optional enhancement* for the Stage Eval Agent's paper-stage
review:

  * ``CSPAPER_API_KEY`` set  → submit the PDF, poll until the async review
    completes, return it.
  * key NOT set / call fails → returns a non-``ok`` status so the agent falls
    back to writing the review itself from ``review_template_en.md``.

The submit endpoint is public; the poll ("Check Results") endpoint is not, so
base URL / submit / poll paths and the venue ``agent_id`` are all configurable
via Settings (``cspaper_*``) or env vars. Ported from the standalone
``cs-paper-review-agent`` client (requests → httpx, which is already a dep).
"""
from __future__ import annotations

import os
import time

import httpx
from langchain_core.tools import tool
from loguru import logger

_TERMINAL_OK = {"COMPLETED", "SUCCESS", "DONE"}
_TERMINAL_BAD = {"FAILED", "ERROR", "CANCELLED"}
_POLL_INTERVAL_SECONDS = 8


def _setting(attr: str, env: str, default: str = "") -> str:
    """Prefer pydantic Settings; fall back to env so the tool also works when
    loaded outside the app process (e.g. a standalone runner)."""
    val = ""
    try:
        from onemancompany.core.config import settings

        val = getattr(settings, attr, "") or ""
    except Exception:
        val = ""
    return val or os.environ.get(env, default)


def _status(payload) -> str | None:
    """Pull a status string out of the payload regardless of nesting."""
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("status"), str):
        return payload["status"]
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("status"), str):
        return data["status"]
    return None


def _job_id(payload) -> str | None:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict) and data.get("job_id"):
        return data["job_id"]
    return payload.get("job_id") if isinstance(payload, dict) else None


@tool
def cspaper_review(file_path: str, agent_id: str = "", poll_timeout: int = 300) -> dict:
    """Get a conference-style peer review of a paper PDF from cspaper.org.

    Submits the PDF to the cspaper.org Agentic Review API and polls until the
    review completes. REQUIRES ``CSPAPER_API_KEY``; if it is not set this
    returns ``status="disabled"`` — in that case write the review yourself
    using the bundled ``review_template_en.md`` instead.

    Args:
        file_path: absolute path to the paper PDF.
        agent_id: venue agent, e.g. "NeurIPS_main_2025_1" (defaults to CSPAPER_AGENT_ID).
        poll_timeout: max seconds to wait for the async review (default 300).

    Returns a dict with ``status`` in {ok, disabled, error, failed, timeout}.
    On ``ok`` the review is under ``review``.
    """
    key = _setting("cspaper_api_key", "CSPAPER_API_KEY")
    if not key:
        return {
            "status": "disabled",
            "message": "No CSPAPER_API_KEY configured. Fall back to writing the "
            "review from review_template_en.md.",
        }
    if not os.path.isfile(file_path):
        return {"status": "error", "message": f"PDF not found: {file_path}"}

    base = _setting(
        "cspaper_base_url",
        "CSPAPER_BASE_URL",
        "https://cspaper-frontend-prod.azurewebsites.net",
    )
    submit_path = _setting("cspaper_submit_path", "CSPAPER_SUBMIT_PATH", "/api/platform/review")
    poll_path = _setting("cspaper_poll_path", "CSPAPER_POLL_PATH", "/api/platform/review/{job_id}")
    agent = agent_id or _setting("cspaper_agent_id", "CSPAPER_AGENT_ID", "NeurIPS_main_2025_1")
    headers = {"X-API-Key": key}

    try:
        with open(file_path, "rb") as fh:
            resp = httpx.post(
                base + submit_path,
                headers=headers,
                data={"agent_id": agent},
                files={"file": (os.path.basename(file_path), fh, "application/pdf")},
                timeout=120,
            )
        submitted = resp.json()
    except Exception as exc:
        logger.warning("[cspaper] submit failed: {}", exc)
        return {"status": "error", "message": f"submit failed: {exc}"}

    jid = _job_id(submitted)
    if not jid:
        return {"status": "error", "message": "no job_id in submit response", "raw": submitted}

    deadline = time.time() + max(30, int(poll_timeout))
    while True:
        try:
            r = httpx.get(base + poll_path.format(job_id=jid), headers=headers, timeout=60)
            payload = r.json()
        except Exception as exc:
            logger.warning("[cspaper] poll failed: {}", exc)
            return {"status": "error", "message": f"poll failed: {exc}", "job_id": jid}

        st = (_status(payload) or "").upper()
        if st in _TERMINAL_OK:
            return {"status": "ok", "job_id": jid, "review": payload}
        if st in _TERMINAL_BAD:
            return {"status": "failed", "job_id": jid, "review": payload}
        if time.time() > deadline:
            return {
                "status": "timeout",
                "job_id": jid,
                "message": f"not COMPLETED within {poll_timeout}s; fall back to the "
                "template review.",
            }
        time.sleep(_POLL_INTERVAL_SECONDS)
