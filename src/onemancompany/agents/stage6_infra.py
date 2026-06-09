"""Deterministic, parameterised Stage-6 experiment submission (#156).

The engine drives the experiment submission itself instead of relying on the
runner agent to invoke the ``experiment-infra`` skill (which it does
unreliably — it stubs, and the gate then auto-approves a paper on no real
data). This module is the engine-side analogue of ``aigraph_grounding`` for
Stage 3: **deterministic but fully parameterised** —

- the runnable entrypoint (smoke / full) is read from the Stage-6a receipt
  (``stage6_implementation_receipt.md``), NOT hardcoded;
- the submission uses the ``experiment-infra`` skill's OWN scripts
  (``fast_push_code.sh`` / ``fast_submit.sh`` / ``fast_query_exp_status.sh``) —
  the same contract the agent was supposed to use;
- credentials come from ``INFRA_SERVER_URL`` / ``INFRA_SESSION_KEY`` (env);
- nothing about a specific experiment is baked in.

Never raises — every entry point returns a structured result so the engine can
HOLD (not fake-advance) when infra is unavailable.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# The Stage-6a receipt's "Runnable Entrypoint" section. We pull the first code
# line under a "Smoke" / "Full" heading, and the local code dir + remote dest.
_SMOKE_HEADER = re.compile(r"^#+.*\bsmoke\b", re.IGNORECASE | re.MULTILINE)
_FULL_HEADER = re.compile(r"^#+.*\bfull\b", re.IGNORECASE | re.MULTILINE)
_LOCAL_FILE = re.compile(r"(?:local file|local path)[^\n]*?(/[\w./+-]+)", re.IGNORECASE)
_REMOTE_PATH = re.compile(r"(?:remote path|remote dest)[^\n]*?[`'\"]([\w][\w./+-]*)", re.IGNORECASE)
# A run command: a line that invokes the entrypoint (python / bash / accelerate / torchrun).
_CMD_LINE = re.compile(r"^\s*`?((?:cd\s+\S+\s*&&\s*)?(?:python|bash|accelerate|torchrun|uv run)\b[^`\n]+)`?\s*$",
                       re.IGNORECASE | re.MULTILINE)


@dataclass
class Receipt:
    smoke_cmd: str = ""
    full_cmd: str = ""
    code_dir: str = ""       # local dir containing the experiment code
    remote_dest: str = ""    # remote workspace dest (relative)

    @property
    def ok(self) -> bool:
        return bool(self.smoke_cmd or self.full_cmd)


def _first_cmd_after(text: str, header_re: re.Pattern) -> str:
    m = header_re.search(text)
    if not m:
        return ""
    tail = text[m.end():]
    c = _CMD_LINE.search(tail)
    return c.group(1).strip() if c else ""


def parse_receipt(text: str, project_id: str = "", iteration: str = "iter_001") -> Receipt:
    """Extract the runnable entrypoints + code locations from a Stage-6a receipt.

    Fully parameterised — reads what 6a wrote; nothing experiment-specific is
    hardcoded. ``remote_dest`` falls back to ``omc/<pid>/<iter>`` when the
    receipt doesn't name one.
    """
    text = text or ""
    smoke = _first_cmd_after(text, _SMOKE_HEADER)
    full = _first_cmd_after(text, _FULL_HEADER)
    if not smoke and not full:
        # no explicit smoke/full headings — take the first run command in the file
        c = _CMD_LINE.search(text)
        smoke = c.group(1).strip() if c else ""
    lf = _LOCAL_FILE.search(text)
    code_dir = ""
    if lf:
        p = Path(lf.group(1))
        code_dir = str(p.parent if p.suffix else p)
    rp = _REMOTE_PATH.search(text)
    remote_dest = ""
    if rp:
        rp_p = Path(rp.group(1))
        remote_dest = str(rp_p.parent if rp_p.suffix else rp_p)
    if not remote_dest and project_id:
        remote_dest = f"omc/{project_id}/{iteration}"
    return Receipt(smoke_cmd=smoke, full_cmd=full, code_dir=code_dir, remote_dest=remote_dest)


@dataclass
class SubmitResult:
    ok: bool
    run_id: str = ""
    kind: str = ""           # "smoke" | "full"
    raw: dict = field(default_factory=dict)
    error: str = ""


def _creds() -> tuple[str, str]:
    return os.environ.get("INFRA_SERVER_URL", ""), os.environ.get("INFRA_SESSION_KEY", "")


def find_infra_scripts(skill_root: str | None = None) -> dict:
    """Locate the experiment-infra skill scripts. ``skill_root`` overrides; else
    use ``AIGRAPH``-style env ``EXPERIMENT_INFRA_SCRIPTS`` or search common roots.
    Returns {} if not found (caller degrades)."""
    cands: list[Path] = []
    if skill_root:
        cands.append(Path(skill_root))
    env = os.environ.get("EXPERIMENT_INFRA_SCRIPTS")
    if env:
        cands.append(Path(env))
    names = {"fast_push_code.sh", "fast_submit.sh", "fast_query_exp_status.sh"}
    for base in cands:
        if base.is_dir() and names.issubset({p.name for p in base.glob("*.sh")}):
            return {n: str(base / n) for n in names}
    return {}


def _run(args: list[str], env: dict, timeout: float = 320.0) -> tuple[int, str]:
    try:
        r = subprocess.run(args, capture_output=True, text=True, env=env, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return 1, f"{type(exc).__name__}: {exc}"


def _extract_run_id(out: str) -> str:
    import json as _json
    try:
        d = _json.loads(out)
        if isinstance(d, dict) and d.get("run_id"):
            return str(d["run_id"])
    except Exception:  # noqa: BLE001
        pass
    m = re.search(r'"run_id"\s*:\s*"([^"]+)"', out) or re.search(r"\b(run_[0-9a-f]{8,})\b", out)
    return m.group(1) if m else ""


def submit(receipt: Receipt, scripts: dict, config_path: str, kind: str = "smoke",
           env: dict | None = None) -> SubmitResult:
    """Push the code and submit the (smoke|full) run via the skill's scripts.

    Parameterised: pushes ``receipt.code_dir`` to ``receipt.remote_dest`` and
    submits ``receipt.smoke_cmd`` / ``receipt.full_cmd``. Never raises.
    """
    url, key = _creds()
    if not (url and key):
        return SubmitResult(ok=False, kind=kind, error="missing INFRA_SERVER_URL / INFRA_SESSION_KEY")
    if not scripts:
        return SubmitResult(ok=False, kind=kind, error="experiment-infra scripts not found")
    cmd = receipt.full_cmd if kind == "full" else receipt.smoke_cmd
    if not cmd:
        return SubmitResult(ok=False, kind=kind, error=f"no {kind} command in receipt")
    run_env = dict(env or os.environ, INFRA_SERVER_URL=url, INFRA_SESSION_KEY=key)

    if receipt.code_dir and receipt.remote_dest and "push" not in (os.environ.get("STAGE6_SKIP_PUSH") or ""):
        rc, out = _run(["bash", scripts["fast_push_code.sh"], receipt.code_dir, receipt.remote_dest], run_env)
        if rc != 0:
            return SubmitResult(ok=False, kind=kind, error=f"push failed: {out[:200]}")

    rc, out = _run(["bash", scripts["fast_submit.sh"], "--config", config_path, "--cmd", cmd], run_env)
    rid = _extract_run_id(out)
    if rc != 0 or not rid:
        return SubmitResult(ok=False, kind=kind, error=f"submit failed (rc={rc}): {out[:200]}")
    return SubmitResult(ok=True, run_id=rid, kind=kind)


def query_status(run_id: str, scripts: dict, env: dict | None = None) -> dict:
    """Return {status, log_tail, ...} for a run, or {} on failure. Never raises."""
    url, key = _creds()
    if not (url and key and scripts):
        return {}
    run_env = dict(env or os.environ, INFRA_SERVER_URL=url, INFRA_SESSION_KEY=key)
    rc, out = _run(["bash", scripts["fast_query_exp_status.sh"], run_id], run_env, timeout=60)
    import json as _json
    try:
        d = _json.loads(out)
    except Exception:  # noqa: BLE001
        return {}
    if isinstance(d, dict) and d.get("run_id"):
        return d
    for r in (d.get("runs", []) if isinstance(d, dict) else []):
        if r.get("run_id") == run_id:
            return r
    return {}
