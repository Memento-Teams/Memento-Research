"""Deterministic Stage 6 run-authenticity verifier (ZERO LLM).

The hard problem an LLM cannot reliably solve: did the experiment *actually*
succeed, and do the reported numbers match reality? We answer it
deterministically by checking each claimed run against the **authoritative
infra record** (`POST {server_url}/api/status`) — a source the producing agent
cannot forge.

Three deterministic checks per run (no LLM):
  1. **Existence** — the claimed run_id resolves on infra (else fabricated).
  2. **Claimed-vs-authoritative status** — if the report presents a run as a
     *successful* basis for results but infra says it failed / was rejected /
     is still running / does not exist → FAIL. (An honestly-reported failure
     is NOT gated — that is a real negative result for the critic to weigh.)
  3. **Metric fidelity** — when infra exposes numeric ``metrics`` for the run
     AND the report claims matching keys, compare within tolerance; a
     mismatch (cherry-picked / edited numbers) → FAIL. When infra exposes no
     metrics (common today), metric comparison is skipped — never a false fail.

Verdict semantics (designed to GATE without false-blocking):
  - ``fail``        — positive evidence of a problem (checks above). → REJECT.
  - ``unverifiable``— cannot check (no runs / infra unreachable / no creds). → never blocks.
  - ``pass``        — every claimed-successful run exists, succeeded, and (where
                      checkable) its metrics match.

Run_id extraction is delegated to the caller (the pipeline reuses its tested
``_parse_runner_report_runs``); this module never re-implements run_id parsing.
Never logs the session key.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from loguru import logger

_SUCCESS_STATES = {"succeeded", "success", "done", "completed"}
# Statuses that, when *claimed by the report*, mean "this run is presented as a
# successful basis for results" and therefore must match infra. A report that
# honestly says failed/rejected is reporting a real negative — not gated here.
_CLAIMED_SUCCESS = _SUCCESS_STATES | {""}  # "" = unspecified → treat as claimed-success

# Caps: bound the synchronous infra work so a runaway report cannot stall the
# pipeline. Truncation is logged (never silent).
MAX_RUNS = 25
TOTAL_DEADLINE_SECONDS = 90.0
_METRIC_REL_TOL = 0.05  # 5% relative; abs fallback for near-zero values
_METRIC_ABS_TOL = 1e-6

FAIL = "fail"
PASS = "pass"
UNVERIFIABLE = "unverifiable"

# JSON object literals in the report — used to harvest claimed metric numbers.
_JSON_OBJ_RE = re.compile(r"\{[^{}]*\}")


@dataclass
class RunCheck:
    run_id: str
    claimed_status: str
    infra_status: str
    result: str  # PASS | FAIL | UNVERIFIABLE
    evidence: str = ""


@dataclass
class RunVerdict:
    verdict: str
    checks: list[RunCheck] = field(default_factory=list)
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict == PASS

    @property
    def failed(self) -> bool:
        return self.verdict == FAIL


def load_infra_creds(repo_root: Path | None = None) -> tuple[str, str] | None:
    """Resolve (server_url, session_key) from env or the bundled creds file.
    Returns None when unavailable (→ verification skipped, not failed)."""
    import os

    url = os.environ.get("INFRA_SERVER_URL", "")
    key = os.environ.get("INFRA_SESSION_KEY", "")
    if url and key:
        return url, key
    root = repo_root or Path(__file__).resolve().parents[2]
    for cred in (
        root / "onemancompany" / "default_skills" / "experiment-infra" / "experiment_infra_credentials.json",
        root / "src" / "onemancompany" / "default_skills" / "experiment-infra" / "experiment_infra_credentials.json",
    ):
        try:
            if cred.exists():
                d = json.loads(cred.read_text(encoding="utf-8"))
                if d.get("server_url") and d.get("session_key"):
                    return d["server_url"], d["session_key"]
        except Exception as exc:  # noqa: BLE001
            logger.debug("[run-verify] creds load failed: {}", exc)
    return None


def _query_status(server_url: str, session_key: str, run_id: str, timeout: float) -> dict | None:
    """POST /api/status → parsed JSON, or None on transport error. The
    session key travels only in the request body and is never logged."""
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
    except Exception:  # noqa: BLE001 — never surface the key in error text
        logger.debug("[run-verify] /api/status transport error for run_id={}", run_id)
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


def _metrics_of(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    m = payload.get("metrics")
    if isinstance(m, dict):
        return m
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("metrics"), dict):
        return data["metrics"]
    return {}


def extract_claimed_metrics(report_text: str) -> dict[str, float]:
    """Best-effort harvest of numeric metric claims from the report's JSON
    blocks. Coarse (last-wins across the doc) — only used to compare against
    infra metrics that share a key, so over-collection is harmless."""
    claimed: dict[str, float] = {}
    for blk in _JSON_OBJ_RE.findall(report_text or ""):
        try:
            obj = json.loads(blk)
        except Exception:
            # Most brace-blocks in a prose report are not JSON — expected; skip.
            logger.debug("[run-verify] non-JSON block skipped while harvesting metrics")
            continue
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, bool):
                    continue
                if isinstance(v, (int, float)):
                    claimed[str(k)] = float(v)
    return claimed


def _compare_metrics(claimed: dict[str, float], infra: dict) -> list[str]:
    """Return human-readable mismatch strings for keys present in BOTH sides
    whose numeric values differ beyond tolerance. Non-numeric / unshared keys
    are ignored (cannot deterministically compare)."""
    mismatches: list[str] = []
    for k, iv in infra.items():
        if isinstance(iv, bool) or not isinstance(iv, (int, float)):
            continue
        if k not in claimed:
            continue
        cv = claimed[k]
        denom = max(abs(float(iv)), _METRIC_ABS_TOL)
        if abs(float(iv) - cv) / denom > _METRIC_REL_TOL:
            mismatches.append(f"{k}: report={cv} vs infra={iv}")
    return mismatches


def verify(
    runs: list[tuple[str, str]],
    report_text: str = "",
    *,
    creds: tuple[str, str] | None = None,
    timeout: float = 10.0,
    querier: Callable = _query_status,
) -> RunVerdict:
    """Verify ``runs`` = ``[(run_id, claimed_status), ...]`` (as produced by the
    pipeline's ``_parse_runner_report_runs``) against infra. ``report_text`` is
    used only to harvest claimed metric numbers. ``querier`` is injectable so
    tests run fully offline."""
    if not runs:
        return RunVerdict(UNVERIFIABLE, reason="no run_id in deliverable (local run or none claimed)")
    if creds is None:
        creds = load_infra_creds()
    if creds is None:
        return RunVerdict(UNVERIFIABLE, reason="no infra credentials available")

    if len(runs) > MAX_RUNS:
        logger.warning("[run-verify] {} run_ids claimed; verifying first {} (cap)", len(runs), MAX_RUNS)
        runs = runs[:MAX_RUNS]

    server_url, session_key = creds
    claimed_metrics = extract_claimed_metrics(report_text)
    checks: list[RunCheck] = []
    any_bad = False
    any_unverifiable = False
    deadline = time.monotonic() + TOTAL_DEADLINE_SECONDS

    for rid, claimed in runs:
        claimed_l = (claimed or "").lower()
        if time.monotonic() > deadline:
            checks.append(RunCheck(rid, claimed_l, "", UNVERIFIABLE, "verification deadline reached"))
            any_unverifiable = True
            continue
        payload = querier(server_url, session_key, rid, timeout)
        if payload is None:
            checks.append(RunCheck(rid, claimed_l, "", UNVERIFIABLE, "infra unreachable"))
            any_unverifiable = True
            continue
        if "_http" in payload:
            code = payload["_http"]
            if code == 404:
                checks.append(RunCheck(rid, claimed_l, "not_found", FAIL, "infra: run_id not found"))
                any_bad = True
            else:
                checks.append(RunCheck(rid, claimed_l, "", UNVERIFIABLE, f"infra HTTP {code}"))
                any_unverifiable = True
            continue

        infra_status = (_status_of(payload) or "").lower()
        if infra_status == "":
            checks.append(RunCheck(rid, claimed_l, "not_found", FAIL, "infra: run_id not found / no status"))
            any_bad = True
            continue

        # The report honestly reports a non-success terminal status → not a
        # fabrication; leave the scientific call to the critic.
        if claimed_l not in _CLAIMED_SUCCESS:
            checks.append(RunCheck(rid, claimed_l, infra_status, PASS,
                                   f"report claims non-success ({claimed_l}); not gated"))
            continue

        if infra_status not in _SUCCESS_STATES:
            checks.append(RunCheck(rid, claimed_l, infra_status, FAIL,
                                   f"report presents run as successful but infra status is '{infra_status}'"))
            any_bad = True
            continue

        # Run genuinely succeeded → optionally verify metric fidelity.
        infra_metrics = _metrics_of(payload)
        mism = _compare_metrics(claimed_metrics, infra_metrics) if infra_metrics else []
        if mism:
            checks.append(RunCheck(rid, claimed_l, infra_status, FAIL,
                                   "metric mismatch — " + "; ".join(mism[:4])))
            any_bad = True
        else:
            note = "succeeded" if infra_metrics else "succeeded (infra exposes no metrics to cross-check)"
            checks.append(RunCheck(rid, claimed_l, infra_status, PASS, f"infra: {note}"))

    if any_bad:
        bad = [c for c in checks if c.result == FAIL]
        return RunVerdict(FAIL, checks=checks,
                          reason="; ".join(f"{c.run_id}: {c.evidence}" for c in bad))
    if any_unverifiable:
        return RunVerdict(UNVERIFIABLE, checks=checks, reason="could not verify some run_ids")
    return RunVerdict(PASS, checks=checks, reason="all claimed-successful run_ids verified on infra")
