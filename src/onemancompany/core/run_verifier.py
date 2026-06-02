"""Deterministic Stage 6 run-authenticity verifier (ZERO LLM).

The hard problem an LLM cannot solve: did the experiment *actually* run, and
are the reported numbers real? We answer it deterministically by checking the
run_id(s) the Stage 6 deliverable claims against the **authoritative infra
record** (`POST {server_url}/api/status`) — a source the producing agent
cannot forge.

Verdict semantics (designed to GATE without false-blocking):
  - ``fail``        — positive evidence of a problem: a claimed run_id does not
                      exist on infra, or its terminal status is not
                      ``succeeded`` (failed / rejected / still_running). → REJECT.
  - ``unverifiable``— cannot check (no run_id in the report → likely a local
                      run; or infra unreachable). → do NOT block (fail-safe).
  - ``pass``        — every claimed run_id exists on infra and ``succeeded``.

Never logs the session key.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from loguru import logger

# Report lines look like "- run_id: <RID>" / "status: succeeded" per the
# experiment-execution runbook's stage6_experimentalist.md template.
_RUN_ID_RE = re.compile(r"run_id\s*[:=]\s*[`\"']?([A-Za-z0-9_\-./]{2,})[`\"']?", re.IGNORECASE)
_TERMINAL_OK = {"succeeded", "success", "done"}
_TERMINAL_BAD = {"failed", "rejected", "error", "cancelled"}

FAIL = "fail"
PASS = "pass"
UNVERIFIABLE = "unverifiable"


@dataclass
class RunCheck:
    run_id: str
    infra_status: str  # the status infra reports, or "" if unknown
    result: str  # PASS | FAIL | UNVERIFIABLE
    evidence: str = ""


@dataclass
class RunVerdict:
    verdict: str  # PASS | FAIL | UNVERIFIABLE
    checks: list[RunCheck] = field(default_factory=list)
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict == PASS

    @property
    def failed(self) -> bool:
        return self.verdict == FAIL


def extract_run_ids(text: str) -> list[str]:
    """De-duplicated run_id(s) claimed in a Stage 6 deliverable."""
    out: list[str] = []
    seen: set[str] = set()
    for m in _RUN_ID_RE.finditer(text or ""):
        rid = m.group(1)
        # Skip obvious placeholders from the template / prose.
        if rid.lower() in {"none", "n/a", "na", "rid", "run_id", "<rid>"}:
            continue
        if rid not in seen:
            seen.add(rid)
            out.append(rid)
    return out


def load_infra_creds(repo_root: Path | None = None) -> tuple[str, str] | None:
    """Resolve (server_url, session_key) from env or the bundled creds file.

    Returns None if no credentials are available (→ verification is skipped,
    treated as unverifiable rather than a failure)."""
    import os

    url = os.environ.get("INFRA_SERVER_URL", "")
    key = os.environ.get("INFRA_SESSION_KEY", "")
    if url and key:
        return url, key
    # Fall back to the bundled credentials file.
    root = repo_root or Path(__file__).resolve().parents[2]  # src/onemancompany/core -> repo
    cred = root / "onemancompany" / "default_skills" / "experiment-infra" / "experiment_infra_credentials.json"
    if not cred.exists():
        cred = root / "src" / "onemancompany" / "default_skills" / "experiment-infra" / "experiment_infra_credentials.json"
    try:
        if cred.exists():
            d = json.loads(cred.read_text(encoding="utf-8"))
            if d.get("server_url") and d.get("session_key"):
                return d["server_url"], d["session_key"]
    except Exception as exc:  # noqa: BLE001
        logger.debug("[run-verify] creds load failed: {}", exc)
    return None


def _query_status(server_url: str, session_key: str, run_id: str, timeout: float) -> dict | None:
    """POST /api/status → parsed JSON, or None on transport error."""
    try:
        import httpx

        r = httpx.post(
            f"{server_url.rstrip('/')}/api/status",
            json={"session_key": session_key, "run_id": run_id},
            timeout=timeout,
            follow_redirects=False,
        )
        if r.status_code != 200:
            return {"_http": r.status_code}
        return r.json()
    except Exception as exc:  # noqa: BLE001 — never surface the key in errors
        logger.debug("[run-verify] /api/status transport error for {}", run_id)
        return None


def _status_of(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""
    s = payload.get("status")
    if isinstance(s, str):
        return s
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("status"), str):
        return data["status"]
    return ""


def verify_text(
    text: str,
    *,
    creds: tuple[str, str] | None = None,
    timeout: float = 10.0,
    querier: Callable = _query_status,
) -> RunVerdict:
    """Verify the run_id(s) claimed in a Stage 6 deliverable against infra.

    ``querier`` is injected so tests run fully offline."""
    run_ids = extract_run_ids(text)
    if not run_ids:
        return RunVerdict(UNVERIFIABLE, reason="no run_id in deliverable (local run or none claimed)")
    if creds is None:
        creds = load_infra_creds()
    if creds is None:
        return RunVerdict(UNVERIFIABLE, reason="no infra credentials available")

    server_url, session_key = creds
    checks: list[RunCheck] = []
    any_bad = False
    any_unverifiable = False
    for rid in run_ids:
        payload = querier(server_url, session_key, rid, timeout)
        if payload is None:
            checks.append(RunCheck(rid, "", UNVERIFIABLE, "infra unreachable"))
            any_unverifiable = True
            continue
        if "_http" in payload:
            code = payload["_http"]
            if code == 404:
                checks.append(RunCheck(rid, "not_found", FAIL, "infra: run_id not found"))
                any_bad = True
            else:
                checks.append(RunCheck(rid, "", UNVERIFIABLE, f"infra HTTP {code}"))
                any_unverifiable = True
            continue
        status = (_status_of(payload) or "").lower()
        if status in _TERMINAL_OK:
            checks.append(RunCheck(rid, status, PASS, "infra: succeeded"))
        elif status in _TERMINAL_BAD:
            checks.append(RunCheck(rid, status, FAIL, f"infra status: {status}"))
            any_bad = True
        elif status == "":
            checks.append(RunCheck(rid, "unknown", FAIL, "infra: run_id not found / no status"))
            any_bad = True
        else:  # still_running / pending / unknown-but-present
            checks.append(RunCheck(rid, status, FAIL, f"infra status not terminal-ok: {status}"))
            any_bad = True

    if any_bad:
        bad = [c for c in checks if c.result == FAIL]
        return RunVerdict(FAIL, checks=checks,
                          reason="; ".join(f"{c.run_id}: {c.evidence}" for c in bad))
    if any_unverifiable:
        return RunVerdict(UNVERIFIABLE, checks=checks, reason="infra unreachable for some run_ids")
    return RunVerdict(PASS, checks=checks, reason="all claimed run_ids succeeded on infra")


def verify_file(path: str | Path, **kw) -> RunVerdict:
    p = Path(path)
    if not p.exists():
        return RunVerdict(UNVERIFIABLE, reason=f"deliverable not found: {p}")
    return verify_text(p.read_text(encoding="utf-8"), **kw)
