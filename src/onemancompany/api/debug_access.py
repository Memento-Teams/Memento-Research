"""Debug-access layer — full per-run artifact + telemetry exposure.

Built on top of the public kiosk so a collaborator can, for ANY pipeline run,
pull everything needed to analyse/debug it without shell access to the box:

    GET /api/debug/runs                      list every run (+ stage/phase/tokens/has_pdf)
    GET /api/debug/run/{pid}                 full bundle: config, LLM token spend,
                                             sandbox run_ids, artifact index, logs index
    GET /api/debug/run/{pid}/files           complete recursive file tree (incl. internal)
    GET /api/debug/run/{pid}/file?path=...   download ANY artifact (markdown / pdf / jsonl /
                                             yaml / figure / latex log) — internal files too
    GET /api/debug/run/{pid}/logs            merged logs: debug_trace.jsonl + serve-log tail
    GET /api/debug/run/{pid}/bundle          one zip of EVERYTHING (+ debug_manifest.json)

All endpoints are read-only and NOT user-scoped (any collaborator may inspect any
run — that is the point). This port is intended to be PUBLIC, so it is hardened
defensively:

  * Secrets (API/session keys, bearer/JWT tokens, infra IPs/hosts) are redacted
    from every text / log / JSON-text response by default.
  * ``?redact=0`` (raw output) is honoured ONLY from a loopback client or when
    ``OMC_DEBUG_ALLOW_RAW=1`` is set — never for an arbitrary public caller.
  * Credential files (.env / *.pem / id_rsa / .netrc …) are denied entirely.
  * Path traversal and symlink escape out of the run dir are blocked.
  * Active content (.html / .svg / .xml) is served as text/plain with
    ``nosniff`` + a locked-down CSP, so a malicious artifact can't run script
    in a viewer's browser.

The on-disk contract this reads (one iteration dir):
    stage{N}_*.md            per-stage deliverables (Stage 1..9)
    gate_review_stage*.md    critic gate reviews
    *_debate_transcript.md   debate transcripts (Stage 4/5)
    *_v1_draft.md            intermediate drafts
    stage5_assignments.md / stage6_implementation_receipt.md
    *.pdf  /  stage8_pdf/main.pdf            final paper
    *.tex *.bib *.sty *.aux *.log            latex sources + compile logs
    figures/*                                figures
    pipeline_state.yaml                      stage_results (run_<hex> ids live here), phase, config
    task_tree.yaml / nodes/                  engine internals
    debug_trace.jsonl                        per-node execution trace
Iteration cost/tokens live in ``iterations/<iter>.yaml`` (cost.breakdown by employee).
"""

from __future__ import annotations

import io
import math
import os
import re
import socket
import subprocess
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from loguru import logger

debug_router = APIRouter()

ENC = "utf-8"

# Response size guards (the port is public; don't let one request read GBs).
_MAX_INLINE_TEXT = 8 * 1024 * 1024   # 8 MB — bigger text files stream as attachment
_MAX_LOG_TAIL = 5000                 # hard cap on ?tail=
_SNIFF_BYTES = 4096                  # bytes read to decide text-vs-binary
# /bundle caps so one request can't zip an unbounded run into memory/disk
_BUNDLE_MAX_FILES = 5000             # max members in a bundle
_BUNDLE_MAX_BYTES = 512 * 1024 * 1024  # ~512 MB aggregate (pre-compression)
_BUNDLE_MAX_MEMBER = 64 * 1024 * 1024  # skip any single file larger than this
_MAX_TREE_FILES = 20000              # cap the recursive file-tree walk

# ── Suffix maps ──────────────────────────────────────────────────────────────
TEXT_SUFFIXES = {
    ".txt", ".md", ".py", ".js", ".html", ".htm", ".css", ".yaml", ".yml",
    ".json", ".jsonl", ".csv", ".tsv", ".xml", ".sh", ".toml", ".cfg", ".ini",
    ".log", ".rst", ".tex", ".bib", ".sty", ".cls", ".sql", ".r", ".rb", ".go",
    ".java", ".c", ".cpp", ".h", ".hpp", ".rs", ".swift", ".kt", ".ts", ".tsx",
    ".jsx", ".aux", ".out", ".svg",
}
# Binaries we serve with their real media type (everything else text → text/plain).
# NB: .svg / .html / .xml are deliberately NOT here — they are active content and
# are always served as inert text/plain.
_BINARY_MEDIA = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".pdf": "application/pdf",
}
# Of those, the ones the console can preview inline rather than force-download.
_INLINE_BINARY = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"}

# Dirs we never walk into.
_NOISE_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules",
               ".pytest_cache", ".mypy_cache", ".ruff_cache"}
# OS/editor junk — never useful for analysis.
_NOISE_FILES = {".DS_Store", "Thumbs.db", ".gitkeep", "desktop.ini"}

# Inert-content security headers (served on every /file response).
_NOSNIFF = "nosniff"
_TEXT_CSP = "default-src 'none'; style-src 'none'; sandbox; frame-ancestors 'none'"


# ─────────────────────────────────────────────────────────────────────────────
# Credential-file denylist — never serve these, never bundle them, never list them.
# ─────────────────────────────────────────────────────────────────────────────
_DENY_SUFFIXES = (
    ".env", ".pem", ".key", ".crt", ".cer", ".der", ".pfx", ".p12", ".pkcs12",
    ".jks", ".jceks", ".bcfks", ".keystore", ".kdbx", ".kdb", ".asc", ".gpg",
    ".ppk", ".pkl", ".pickle", ".netrc", ".htpasswd", ".p8", ".p7b", ".p7c",
    ".ovpn", ".mobileprovision", ".keychain", ".keytab", ".cnf", ".tfvars",
    ".tfstate", ".tfstate.backup", ".properties",
)
_DENY_NAMES = {
    ".env", ".netrc", ".npmrc", ".pypirc", ".git-credentials", ".htpasswd",
    "credentials", "credentials.json", "credentials.yaml", "credentials.yml",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "identity",
    "authorized_keys", "known_hosts",
    "secrets.json", "secrets.yaml", "secrets.yml", ".dockercfg",
    ".pgpass", "pgpass", ".pg_service.conf", ".my.cnf", "my.cnf",
    "shadow", "master.key", "config.json",
    # kube / cloud-cli credential files
    "kubeconfig", "kube-config.yaml", "kube-config.yml", "admin.conf", "config",
    ".boto", ".s3cfg", "rclone.conf", "vault-token", ".vault-token",
    ".terraformrc", "terraform.rc", ".dockerconfigjson", "_netrc",
    "dockerconfig.json", ".dockerconfig.json", "adc.json", "access_tokens.db",
    "application_default_credentials.json", "database.yml", "database.yaml",
    # bare token files (kept narrow so ML artifacts like tokenizer.json,
    # token_usage.md, tokens_used.json are NOT denied)
    "token.txt", "tokens.txt", "token.json", "tokens.json", ".token", ".tokens",
    # IaC / packaging / cloud-cli / app-config credential files (dot & dotless)
    "terraform.tfvars", "terraform.tfstate", "terraform.tfstate.backup",
    "serverless.yml", "serverless.yaml", "wrangler.toml",
    "npmrc", "pip.conf", "pypirc", "boto", "s3cfg", "dockercfg",
    "env", "environment", "conn.ini", "db_connection.txt",
    "application.yml", "application.yaml", "application.properties",
    "kubeconfig.yaml", "kubeconfig.yml", "cluster.conf", "auth.json",
    "config.toml",
}
# substrings that, anywhere in the name (case-insensitive), mark a credential
# file. Safe for real run dirs (stage*.md / gate_review*.md / *.tex / figures/…
# contain none of these words); errs toward not-serving a suspicious file.
_DENY_SUBSTR = (
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credential",
    "service-account", "service_account", "serviceaccount", "client_secret",
    "client-secret", "sa-key", "secret", "password", "passwd", "passphrase",
    "vault", "apikey", "api_key", "htpasswd", "pgpass", "pg_service",
    "connstr", "connection_string", "connection-string", "database-url",
    "database_url", "dburl", "dockerconfig", "legacy_credentials",
    "auth_token", "access_token", "refresh_token", "bearer_token",
)
# path components that mark a credential directory — everything under is denied
_DENY_DIRS = {".ssh", ".aws", ".kube", ".gnupg", ".gpg", ".docker", "gcloud",
              ".config", ".chef", ".azure", ".oci"}
# content sniff: a textual artifact carrying a private key is never served
_RE_PEM_PRIVATE = re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")


def _is_denied(name: str) -> bool:
    """True if this filename looks like a credential/secret store."""
    low = (name or "").lower()
    if not low:
        return True
    if low in _DENY_NAMES:
        return True
    # .env in any position: .env / .env.local / config.env.json / backup.env
    if low.startswith(".env") or ".env." in low or low.endswith(".env"):
        return True
    if low.endswith(_DENY_SUFFIXES):
        return True
    return any(sub in low for sub in _DENY_SUBSTR)


def _path_is_denied(rel: str) -> bool:
    """Deny by filename OR by sitting inside a known credential directory."""
    parts = rel.replace("\\", "/").split("/")
    if any(seg.lower() in _DENY_DIRS for seg in parts[:-1]):
        return True
    return _is_denied(parts[-1] if parts else rel)


def _content_has_private_key(path: Path) -> bool:
    """Sniff the head of a file for a literal PEM private-key block.

    Catches odd-named key files (deploy_key, identity, server_rsa), inline keys
    in .ovpn/config, and pretty-printed SA JSON regardless of the filename.
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(_SNIFF_BYTES)
    except OSError:
        return False
    if b"\x00" in head:
        return False
    try:
        return bool(_RE_PEM_PRIVATE.search(head.decode(ENC, errors="ignore")))
    except Exception:
        return False


# A secrets dump: ≥2 KEY=VALUE / KEY: VALUE lines whose keys are UNAMBIGUOUSLY
# secret. Deliberately conservative — excludes bare 'token'/'_key'/'auth' which
# collide with ML/research content (max_tokens:, sort_key:, author:, token_count:),
# so experiment code / stage markdown / pipeline_state.yaml are never over-denied.
# Named config files (application.yml/.properties/*.tfstate) are denied by name
# anyway; this is the backstop for odd-named dumps. Accepts '=' and ':'.
_RE_ENVDUMP_LINE = re.compile(
    r"(?im)^[ \t\"']*(?:export[ \t]+)?[\w.\-]*?"
    r"(?:password|passwd|passphrase|secret|credential|apikey|api[_-]key|"
    r"access[_-]?key|secret[_-]?key|private[_-]?key|client[_-]?secret|"
    r"aws_secret|_pw)"
    r"[A-Za-z0-9_.\-]*\"?[ \t]*[:=][ \t]*[\"']?\S")


def _content_is_secret_dump(path: Path) -> bool:
    """True if a text file's head looks like a dotenv/secrets KEY=VALUE dump.

    Catches credential files with innocuous or extension-less names ('env',
    'environment', a stray '.tfvars'-style file) without an allowlist.
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(_SNIFF_BYTES)
    except OSError:
        return False
    if b"\x00" in head:
        return False
    try:
        text = head.decode(ENC, errors="ignore")
    except Exception:
        return False
    return len(_RE_ENVDUMP_LINE.findall(text)) >= 2


# ─────────────────────────────────────────────────────────────────────────────
# Secret redaction
# ─────────────────────────────────────────────────────────────────────────────
# Env-var name hints whose *values* should be scrubbed wherever they appear.
_SECRET_KEY_HINTS = (
    "API_KEY", "SESSION_KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD",
    "ACCESS_KEY", "PRIVATE_KEY", "APP_SECRET", "CLIENT_SECRET", "AUTH",
)
# Infra-locator hints — values are internal URLs/hosts/IPs we don't want leaked.
_LOCATOR_KEY_HINTS = (
    "SERVER_URL", "BASE_URL", "ENDPOINT", "INFRA", "SANDBOX", "_HOST", "HOSTNAME",
    "PROXY", "LITELLM", "OPENAI_BASE", "API_BASE",
)
_RE_SK = re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}\b")
# key=value / key: value where the KEY (optionally prefixed, e.g. db_admin_pw,
# signing_key) ends in a secret-ish token. The suffix must sit immediately before
# the [:=], so 'max_tokens:' / 'token_usage:' (token counts) are NOT redacted.
_RE_KV = re.compile(
    r'(?i)("?[\w.\-]*?(?:'
    r'password|passwd|passphrase|pwd|'
    r'secret|token|credential|authorization|bearer|'
    r'api[_-]?key|access[_-]?key|secret[_-]?key|private[_-]?key|priv[_-]?key|'
    r'signing[_-]?key|session[_-]?key|app[_-]?secret|client[_-]?secret|'
    r'client[_-]?key[_-]?data|client[_-]?certificate[_-]?data|'
    r'_pw|_pass|_key|_secret|_token'
    r')"?\s*[:=]\s*"?)'
    r'([^\s"\',;}\)]+)'
)
# multi-line PEM key/cert blocks → scrub the whole block
_RE_PEM_BLOCK = re.compile(r"-----BEGIN [A-Z0-9 ]+-----.*?-----END [A-Z0-9 ]+-----",
                           re.DOTALL)
# user:pass@host inside any scheme://… URI — redact creds AND the authority,
# since embedded creds mark the whole connection string as sensitive.
# username optional → also catches redis://:password@host (password-only authority)
_RE_URI_CRED = re.compile(
    r"\b([A-Za-z][A-Za-z0-9+.\-]*://)([^/\s:@]*):([^/\s@]+)@([^/\s]+)")
# cloud / provider token shapes (no key= prefix needed) — all applied unconditionally
_RE_AWS_KEYID = re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA|AIPA)[0-9A-Z]{12,}\b")
_RE_SLACK = re.compile(
    r"\bxox[baprse]-[A-Za-z0-9-]{8,}|\bxapp-[A-Za-z0-9-]{8,}|\bxoxe\.[A-Za-z0-9-]{8,}")
_RE_GH = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
_RE_GOOGLE_API = re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}\b")
_RE_VAULT = re.compile(r"\b(?:hv[sb]\.[A-Za-z0-9_\-]{12,}|s\.[A-Za-z0-9]{20,})\b")
_RE_STRIPE = re.compile(r"\b(?:[sr]k|whsec)_(?:live|test)?_?[A-Za-z0-9]{16,}\b")
_RE_SENDGRID = re.compile(r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}\b")
_RE_TWILIO = re.compile(r"\bAC[0-9a-fA-F]{32}\b")
_RE_GITLAB = re.compile(r"\bglpat-[A-Za-z0-9_\-]{16,}\b")
_RE_AZURE_ACCTKEY = re.compile(r"(?i)\bAccountKey=[A-Za-z0-9+/=]{20,}")
# .pgpass line: host:port:db:user:PASSWORD — fields are whitespace-free, so a
# prose/table row with colons (e.g. "08:41:55 → 08:48:55") won't match.
_RE_PGPASS = re.compile(
    r"(?m)^([^:\s\n]*:(?:\d{1,5}|\*):[^:\s\n]*:[^:\s\n]*:)([^\s\n]+)\s*$")
# octet-bounded IPv4 (won't match arbitrary 4-number version strings as eagerly)
_RE_IPV4 = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"
)
# IPv6 — only real addresses (8 groups, or a '::' compression); won't eat MACs/times
_RE_IPV6 = re.compile(
    r"\b(?:"
    r"(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}"
    r"|(?:[0-9A-Fa-f]{1,4}:){1,7}:"
    r"|(?:[0-9A-Fa-f]{1,4}:){1,6}:[0-9A-Fa-f]{1,4}"
    r"|(?:[0-9A-Fa-f]{1,4}:){1,5}(?::[0-9A-Fa-f]{1,4}){1,2}"
    r"|(?:[0-9A-Fa-f]{1,4}:){1,4}(?::[0-9A-Fa-f]{1,4}){1,3}"
    r"|(?:[0-9A-Fa-f]{1,4}:){1,3}(?::[0-9A-Fa-f]{1,4}){1,4}"
    r"|(?:[0-9A-Fa-f]{1,4}:){1,2}(?::[0-9A-Fa-f]{1,4}){1,5}"
    r"|[0-9A-Fa-f]{1,4}:(?::[0-9A-Fa-f]{1,4}){1,6}"
    r"|:(?:(?::[0-9A-Fa-f]{1,4}){1,7}|:)"
    r")\b"
)
# bearer/basic <token> — require a token-like value (≥16 chars AND a digit/+/=)
# so prose like "basic arithmetic" is not mistaken for a Basic-auth credential.
_RE_BEARER = re.compile(
    r"(?i)\b(bearer|basic)\s+(?=[A-Za-z0-9_\-./+=]*[0-9+/=])[A-Za-z0-9_\-./+=]{16,}")
_RE_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\b")
# internal hostnames
_RE_INT_HOST = re.compile(
    r"\b[A-Za-z0-9_\-]+(?:\.[A-Za-z0-9_\-]+)*\.(?:internal|local|lan|corp|intranet)\b"
)
# high-entropy blobs — scrubbed only on a secret-context line (see redact_text),
# so citation-URL hashes / git shas / md5 checksums in prose stay readable.
# run_<hex> ids survive anyway ('_' breaks the leading word boundary).
_RE_HEX_BLOB = re.compile(r"\b[0-9a-fA-F]{32,128}\b")
_RE_B64_BLOB = re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b")
_RE_SECRET_HINT = re.compile(
    r"(?i)(secret|token|api[_-]?key|password|passwd|passphrase|credential|"
    r"\bauth\b|bearer|\bkey\b|access[_-]?key|private|akia|aws_)"
)


def _env_secret_values() -> list[str]:
    """Literal secret / infra-locator values from the environment, longest-first.

    Redacting the actual values catches secrets even when they appear without a
    recognisable ``key=`` prefix (e.g. an infra session key or base URL pasted
    verbatim into a log line).
    """
    vals: set[str] = set()
    for k, v in os.environ.items():
        if not v:
            continue
        ku = k.upper()
        is_secret = any(h in ku for h in _SECRET_KEY_HINTS)
        is_locator = any(h in ku for h in _LOCATOR_KEY_HINTS)
        if not (is_secret or is_locator):
            continue
        # short floor for clearly-secret keys (catch e.g. a 4-char PIN); higher
        # floor for ambiguous locator values to avoid over-redaction.
        if len(v) < (4 if is_secret else 6):
            continue
        if is_secret or is_locator:
            vals.add(v)
            # also scrub the bare host:port inside a URL value
            m = re.search(r"https?://([^/\s]+)", v)
            if m and len(m.group(1)) >= 6:
                vals.add(m.group(1))
    return sorted(vals, key=len, reverse=True)


def redact_text(s: str, enabled: bool = True) -> str:
    """Scrub API/session keys, bearer/JWT tokens, internal hosts and IPs.

    Conservative and idempotent; only touches obvious secret/locator shapes so
    debug output stays readable. Disable with ``?redact=0`` on a trusted network
    (loopback / OMC_DEBUG_ALLOW_RAW only — never honoured for a public caller).
    """
    if not enabled or not s:
        return s
    # 1. whole PEM blocks first (before line-wise / KV passes touch the body)
    s = _RE_PEM_BLOCK.sub("[redacted-pem]", s)
    # 2. literal env secret/locator values
    for val in _env_secret_values():
        if val:
            s = s.replace(val, "[redacted-secret]")
    # 3. provider token + URI-credential shapes (no key= prefix needed)
    s = _RE_AWS_KEYID.sub("[redacted-aws-key]", s)
    s = _RE_GH.sub("[redacted-gh-token]", s)
    s = _RE_SLACK.sub("[redacted-slack-token]", s)
    s = _RE_GOOGLE_API.sub("[redacted-key]", s)
    s = _RE_VAULT.sub("[redacted-token]", s)
    s = _RE_STRIPE.sub("[redacted-key]", s)
    s = _RE_SENDGRID.sub("[redacted-key]", s)
    s = _RE_GITLAB.sub("[redacted-token]", s)
    s = _RE_TWILIO.sub("[redacted-key]", s)
    s = _RE_AZURE_ACCTKEY.sub("AccountKey=[redacted]", s)
    s = _RE_SK.sub("[redacted-key]", s)
    s = _RE_JWT.sub("[redacted-jwt]", s)
    s = _RE_URI_CRED.sub(lambda m: m.group(1) + "[redacted-cred]@[redacted-host]", s)
    s = _RE_BEARER.sub(lambda m: m.group(1) + " [redacted-token]", s)
    s = _RE_KV.sub(lambda m: m.group(1) + "[redacted]", s)
    s = _RE_PGPASS.sub(lambda m: m.group(1) + "[redacted]", s)
    # 4. internal hosts + IPs
    s = _RE_INT_HOST.sub("[redacted-host]", s)
    s = _RE_IPV6.sub("[redacted-ip]", s)
    s = _RE_IPV4.sub("[redacted-ip]", s)
    # 5. high-entropy hex/base64 blobs — only on a secret-context line (a hint
    #    word, or a line where an AWS AKIA id was just redacted). This catches the
    #    bare 40-char AWS secret next to its key id and generic keys near a hint,
    #    without mangling citation-URL hashes / git shas / timestamps in prose.
    if _RE_SECRET_HINT.search(s) or "[redacted-aws-key]" in s:
        out_lines = []
        for ln in s.split("\n"):
            if _RE_SECRET_HINT.search(ln) or "[redacted-aws-key]" in ln:
                ln = _RE_HEX_BLOB.sub("[redacted-secret]", ln)
                ln = _RE_B64_BLOB.sub("[redacted-secret]", ln)
            out_lines.append(ln)
        s = "\n".join(out_lines)
    return s


def _redact_scalar(v, enabled: bool):
    """Redact a scalar config value if it's a string."""
    if isinstance(v, str):
        return redact_text(v, enabled)
    return v


# ─────────────────────────────────────────────────────────────────────────────
# redact gating — raw output ONLY via an explicit server-side opt-in
# ─────────────────────────────────────────────────────────────────────────────
def _effective_redact(redact_param: int, request: Request | None = None) -> bool:
    """Resolve the redaction flag.

    Redaction is ON by default. ``redact=0`` (raw) is honoured ONLY when the
    server operator sets ``OMC_DEBUG_ALLOW_RAW=1`` — never inferred from the
    client IP. The whole point is that this box is reached over an ``ssh -L``
    tunnel, so every real caller appears as 127.0.0.1; trusting loopback (or an
    X-Forwarded-For header) would silently disable redaction for exactly the
    people it must protect against. ``request`` is accepted for signature
    compatibility but deliberately ignored.
    """
    if redact_param:
        return True
    return os.environ.get("OMC_DEBUG_ALLOW_RAW") != "1"


# ─────────────────────────────────────────────────────────────────────────────
# Resolution helpers
# ─────────────────────────────────────────────────────────────────────────────
def _resolve(project_id: str, iteration: str = "") -> str:
    """Return the qualified identifier (``slug`` or ``slug/iter_NNN``)."""
    iteration = (iteration or "").strip()
    return f"{project_id}/{iteration}" if iteration else project_id


def _project_dir(project_id: str, iteration: str = "") -> Path | None:
    """Resolve a run's iteration dir, or None if no real run exists.

    We validate existence *before* calling ``get_project_dir`` because that
    helper mkdir's an empty fallback dir for unknown ids (which would otherwise
    make a bogus project return 200 and litter PROJECTS_DIR). A named project
    with zero iterations resolves to None (no run to inspect → 404).
    """
    from onemancompany.core.project_archive import (
        get_project_dir, load_named_project, load_iteration,
    )
    iteration = (iteration or "").strip()
    if "\x00" in project_id or "/" in project_id or "\\" in project_id:
        return None
    if iteration:
        if not re.match(r"^iter_\d{3,}$", iteration):
            return None
        if load_iteration(project_id, iteration) is None:
            return None
    else:
        proj = load_named_project(project_id)
        if proj is None:
            return None
        iters = proj.get("iterations")
        # must be a non-empty LIST (a truthy non-list would wrongly pass the gate)
        if not isinstance(iters, list) or not iters:
            return None
        # the latest iteration record must actually exist on disk (else 404,
        # not a bare auto-mkdir'd project root)
        if load_iteration(project_id, iters[-1]) is None:
            return None
    try:
        d = get_project_dir(_resolve(project_id, iteration))
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("[debug] get_project_dir({}) raised: {}", project_id, exc)
        return None
    p = Path(d) if d else None
    return p if (p and p.exists()) else None


def _safe_target(pdir: Path, relpath: str) -> Path | None:
    """Resolve ``relpath`` strictly inside ``pdir``; None on traversal/escape.

    Blocks: null bytes, absolute paths, ``..`` escape, and symlinks that resolve
    outside the run dir (``resolve()`` follows links, so an escaping symlink lands
    outside ``root`` and fails the containment check). The final component being a
    symlink is rejected outright as defence in depth.
    """
    if not relpath or "\x00" in relpath:
        return None
    rp = relpath.replace("\\", "/")
    if rp.startswith("/"):           # reject absolute paths outright
        return None
    candidate = pdir / rp
    try:
        resolved = candidate.resolve()
        root = pdir.resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    try:
        if candidate.is_symlink():
            return None
    except OSError:
        return None
    return resolved


def serve_version() -> dict:
    """Best-effort build identity so a downloaded run is traceable to the code."""
    out: dict = {}
    try:
        from importlib.metadata import version
        out["app_version"] = version("onemancompany")
    except Exception:
        out["app_version"] = "dev"
    try:
        from onemancompany.core.config import SOURCE_ROOT
        cwd = str(SOURCE_ROOT)
    except Exception:
        cwd = None
    for label, args in (("git_sha", ["rev-parse", "--short", "HEAD"]),
                        ("git_branch", ["rev-parse", "--abbrev-ref", "HEAD"])):
        try:
            r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                               text=True, timeout=3)
            if r.returncode == 0:
                out[label] = r.stdout.strip()
        except Exception:
            pass
    try:
        out["host"] = socket.gethostname()
    except Exception:
        pass
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Pure builders (path-based → unit-testable against fixtures)
# ─────────────────────────────────────────────────────────────────────────────
def _read_pipeline_state(pdir: Path) -> dict:
    import yaml
    p = pdir / "pipeline_state.yaml"
    if not p.exists():
        return {}
    try:
        doc = yaml.safe_load(p.read_text(encoding=ENC)) or {}
        return doc if isinstance(doc, dict) else {}
    except Exception as exc:
        logger.warning("[debug] bad pipeline_state {}: {}", p, exc)
        return {}


_RE_RUN_ID = re.compile(r"\brun_[0-9a-f]{8,}\b")


def extract_run_ids(state: dict) -> dict:
    """Pull infra/sandbox ``run_<hex>`` ids out of the stage_results blobs.

    Returns ``{"all": [...], "by_stage": {stage: [...]}}`` preserving first-seen
    order. Stage 6 (experiment execution) is where these normally appear.
    """
    by_stage: dict[str, list[str]] = {}
    seen: list[str] = []
    if not isinstance(state, dict):
        return {"all": seen, "by_stage": by_stage}
    results = state.get("stage_results", {})
    if isinstance(results, dict):
        for stage, blob in results.items():
            if not isinstance(blob, str):
                continue
            found: list[str] = []
            for rid in _RE_RUN_ID.findall(blob):
                if rid not in found:
                    found.append(rid)
                if rid not in seen:
                    seen.append(rid)
            if found:
                by_stage[str(stage)] = found
    crit = (state or {}).get("critic_result")
    if isinstance(crit, str):
        for rid in _RE_RUN_ID.findall(crit):
            if rid not in seen:
                seen.append(rid)
    return {"all": seen, "by_stage": by_stage}


def _safe_int(v, default: int = 0) -> int:
    # NB: int(float('inf')) raises OverflowError, int(float('nan')) raises
    # ValueError — both must be caught so a corrupt YAML count can't 500 a route.
    if isinstance(v, float) and not math.isfinite(v):
        return default
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_float(v, default: float = 0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _json_safe(obj):
    """Recursively replace non-finite floats (inf/nan) with None so the value is
    strict-JSON serialisable (Starlette's JSONResponse uses allow_nan=False and
    would 500 on a .inf/.nan that crept in from a corrupt pipeline_state.yaml)."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def aggregate_tokens(iter_doc: dict) -> dict:
    """LLM token + cost rollup from an iteration's ``cost`` block.

    Returns total, per-employee (with resolved name/role) and per-model
    breakdowns. Defensive against malformed cost records (non-dict entries,
    string token counts, a breakdown that isn't a list).
    """
    if not isinstance(iter_doc, dict):
        iter_doc = {}
    cost = iter_doc.get("cost", {})
    if not isinstance(cost, dict):
        cost = {}
    tok = cost.get("token_usage", {})
    if not isinstance(tok, dict):
        tok = {}
    breakdown = cost.get("breakdown", [])
    if not isinstance(breakdown, list):
        breakdown = []

    by_emp: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    n_entries = 0
    for e in breakdown:
        if not isinstance(e, dict):
            continue
        n_entries += 1
        eid = str(e.get("employee_id", "") or "?")
        it, ot = _safe_int(e.get("input_tokens")), _safe_int(e.get("output_tokens"))
        tt = _safe_int(e.get("total_tokens"), it + ot)
        c = _safe_float(e.get("cost_usd"))
        model = str(e.get("model", "") or "?")

        be = by_emp.setdefault(eid, {"employee_id": eid, "name": "", "role": "",
                                     "calls": 0, "input": 0, "output": 0,
                                     "total": 0, "cost_usd": 0.0, "models": set()})
        be["calls"] += 1
        be["input"] += it
        be["output"] += ot
        be["total"] += tt
        be["cost_usd"] += c
        be["models"].add(model)

        bm = by_model.setdefault(model, {"model": model, "calls": 0, "input": 0,
                                         "output": 0, "total": 0, "cost_usd": 0.0})
        bm["calls"] += 1
        bm["input"] += it
        bm["output"] += ot
        bm["total"] += tt
        bm["cost_usd"] += c

    # Resolve employee names/roles (best-effort).
    try:
        from onemancompany.core.store import load_employee, load_ex_employees
        ex = None
        for eid, rec in by_emp.items():
            emp = load_employee(eid)
            if not emp:
                ex = ex if ex is not None else load_ex_employees()
                emp = ex.get(eid, {}) if isinstance(ex, dict) else {}
            emp = emp or {}
            rec["name"] = emp.get("name", "") or emp.get("nickname", "")
            rec["role"] = emp.get("title", "") or emp.get("department", "")
    except Exception as exc:  # pragma: no cover
        logger.debug("[debug] employee resolve failed: {}", exc)

    for rec in by_emp.values():
        rec["models"] = sorted(m for m in rec["models"] if m and m != "?")
        rec["cost_usd"] = round(rec["cost_usd"], 6)
    for rec in by_model.values():
        rec["cost_usd"] = round(rec["cost_usd"], 6)

    return {
        "total": {
            "input": _safe_int(tok.get("input")),
            "output": _safe_int(tok.get("output")),
            "total": _safe_int(tok.get("total")),
            "cost_usd": round(_safe_float(cost.get("actual_cost_usd")), 6),
            "calls": n_entries,
        },
        "by_employee": sorted(by_emp.values(), key=lambda r: r["total"], reverse=True),
        "by_model": sorted(by_model.values(), key=lambda r: r["total"], reverse=True),
        "models": sorted(by_model.keys()),
    }


def _categorize(rel: str) -> str:
    """Classify a relative path into an artifact category for the overview."""
    relp = "/" + rel.replace("\\", "/")
    if "/nodes/" in relp:
        return "engine_internals"
    name = Path(rel).name.lower()
    if name in ("pipeline_state.yaml", "task_tree.yaml"):
        return "pipeline_state"
    if name == "debug_trace.jsonl" or name.endswith(".jsonl"):
        return "trace"
    if name.startswith("gate_review"):
        return "gate_reviews"
    if "debate_transcript" in name:
        return "transcripts"
    if name.endswith("_draft.md") or "_v1_draft" in name:
        return "drafts"
    if name.endswith("_receipt.md") or name.endswith("_assignments.md"):
        return "receipts"
    # figures FIRST — a figure that happens to be a .pdf must not be mislabelled.
    if "/figures/" in relp or name.endswith((".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp")):
        return "figures"
    if name.endswith(".pdf"):
        return "paper_pdf"
    if name.endswith((".tex", ".bib", ".sty", ".cls", ".aux", ".out")) or \
       (name.endswith(".log") and "omc_" not in name):
        return "paper_sources"
    if re.match(r"^stage\d", name) and name.endswith(".md"):
        return "stage_outputs"
    return "other"


def _iter_files(pdir: Path, include_internal: bool):
    """Yield (Path, rel_str) for every *safe* file under pdir.

    Skips noise dirs/files, symlinks (no following links out of the run dir) and
    denied credential files. Confirms each path stays inside the run root.
    """
    root = pdir.resolve()
    yielded = 0
    for p in sorted(pdir.rglob("*")):
        if yielded >= _MAX_TREE_FILES:        # bound a pathological run dir
            logger.warning("[debug] file tree truncated at {} entries for {}",
                           _MAX_TREE_FILES, pdir)
            break
        try:
            if p.is_symlink() or not p.is_file():
                continue
        except OSError:
            continue
        try:
            rel_parts = p.relative_to(pdir).parts
        except ValueError:
            continue
        if set(rel_parts) & _NOISE_DIRS:
            continue
        if p.name in _NOISE_FILES:
            continue
        rel = "/".join(rel_parts)
        # credential file (by name) or credential directory (by path component)
        if _path_is_denied(rel):
            continue
        # containment: real path must stay under the run root
        try:
            p.resolve().relative_to(root)
        except (OSError, ValueError):
            continue
        if not include_internal and rel.startswith("nodes/"):
            continue
        # textual artifact carrying a PEM private key / dotenv secret dump —
        # never list/serve/bundle it (catches odd-named credential files)
        if p.suffix.lower() not in _BINARY_MEDIA and (
                _content_has_private_key(p) or _content_is_secret_dump(p)):
            continue
        yielded += 1
        yield p, rel


def build_file_tree(pdir: Path, include_internal: bool = True) -> list[dict]:
    out = []
    for p, rel in _iter_files(pdir, include_internal):
        suffix = p.suffix.lower()
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        out.append({
            "path": rel,
            "name": p.name,
            "size": size,
            "is_text": (suffix not in _BINARY_MEDIA),
            "category": _categorize(rel),
        })
    return out


def categorize_artifacts(pdir: Path) -> dict:
    cats: dict[str, list[dict]] = {}
    for entry in build_file_tree(pdir, include_internal=True):
        cats.setdefault(entry["category"], []).append({
            "path": entry["path"], "name": entry["name"], "size": entry["size"],
        })
    return cats


def _stage_sort_key(s: str):
    """Numeric-aware stage sort: '6' before '6_impl' before '7'."""
    m = re.match(r"(\d+)(.*)", str(s))
    if m:
        return (int(m.group(1)), m.group(2))
    return (10_000, str(s))


def build_run_info(project_id: str, iteration: str, pdir: Path,
                   proj_doc: dict | None, iter_doc: dict | None,
                   redact: bool = True) -> dict:
    """Assemble the full per-run debug bundle (JSON). Free-text fields are
    redacted when ``redact`` is True; run_ids/tokens/sizes are never redacted."""
    state = _read_pipeline_state(pdir)
    iter_doc = iter_doc if isinstance(iter_doc, dict) else {}
    proj_doc = proj_doc if isinstance(proj_doc, dict) else {}

    iter_id = iter_doc.get("iteration_id") or iteration or ""
    if not iter_id and re.match(r"^iter_\d+$", pdir.name):
        iter_id = pdir.name

    # Config: explicit knobs + every scalar left in pipeline_state (string values
    # redacted — a topic/task pasted from a private brief can carry a key).
    scalar_state = {
        k: _redact_scalar(v, redact)
        for k, v in state.items()
        if k not in ("stage_results", "critic_result", "stage_assignments")
        and isinstance(v, (str, int, float, bool))
    }
    tokens = aggregate_tokens(iter_doc)
    config = {
        "topic": redact_text(str(state.get("topic") or iter_doc.get("task", "")), redact),
        "task": redact_text(str(iter_doc.get("task", "")), redact),
        "start_stage": state.get("start_stage"),
        "end_stage": state.get("end_stage"),
        "auto_approve": state.get("auto_approve"),
        "models": tokens["models"],
        "pipeline_state": scalar_state,
    }

    sandbox = extract_run_ids(state)
    sandbox["infra_configured"] = bool(
        os.environ.get("INFRA_SERVER_URL") and os.environ.get("INFRA_SESSION_KEY")
    )

    crit = state.get("critic_result")
    crit_text = redact_text(crit[:2000], redact) if isinstance(crit, str) else ""

    artifacts = categorize_artifacts(pdir)
    if redact:
        # a secret can hide in a filename too — sibling free-text fields are
        # redacted, so redact path/name here for parity.
        for entries in artifacts.values():
            for a in entries:
                a["path"] = redact_text(a["path"], True)
                a["name"] = redact_text(a["name"], True)
    pdf_candidates = [a["path"] for a in artifacts.get("paper_pdf", [])]

    trace = pdir / "debug_trace.jsonl"
    trace_info: dict = {"present": trace.exists()}
    if trace.exists():
        try:
            sz = trace.stat().st_size
            trace_info["size"] = sz
            # bounded line count — don't full-scan a multi-GB trace per request
            if sz <= _MAX_INLINE_TEXT:
                with trace.open("rb") as fh:
                    trace_info["lines"] = sum(1 for _ in fh)
            else:
                trace_info["lines"] = None
                trace_info["line_count_skipped"] = "file too large"
        except OSError:
            pass

    serve = serve_version()
    if redact:
        serve.pop("host", None)
        try:
            from onemancompany.core.config import PROJECTS_DIR
            shown_dir = str(pdir.resolve().relative_to(PROJECTS_DIR.resolve().parent))
        except Exception:
            shown_dir = pdir.name
    else:
        shown_dir = str(pdir)

    return {
        "meta": {
            "project_id": project_id,
            "name": redact_text(str(proj_doc.get("name", project_id)), redact),
            "iteration_id": iter_id,
            "status": iter_doc.get("status") or proj_doc.get("status", ""),
            "created_at": iter_doc.get("created_at") or proj_doc.get("created_at", ""),
            "completed_at": iter_doc.get("completed_at"),
            "current_owner": iter_doc.get("current_owner", ""),
            "project_dir": shown_dir,
            "serve": serve,
        },
        "config": config,
        "pipeline": {
            "current_stage": state.get("current_stage"),
            "phase": state.get("phase"),
            "retries": state.get("retries", 0),
            "exec_retries": state.get("exec_retries"),
            "impl_retries": state.get("impl_retries"),
            "stages_completed": sorted(
                (str(k) for k in (state.get("stage_results")
                                  if isinstance(state.get("stage_results"), dict) else {})),
                key=_stage_sort_key,
            ),
            "critic_result": crit_text,
        },
        "tokens": tokens,
        "sandbox": sandbox,
        "artifacts": artifacts,
        "pdf": pdf_candidates,
        "logs": {
            "debug_trace": trace_info,
            "serve_log_available": True,
        },
        "download": {
            "files": f"/api/debug/run/{project_id}/files",
            "file": f"/api/debug/run/{project_id}/file?path=",
            "logs": f"/api/debug/run/{project_id}/logs",
            "bundle": f"/api/debug/run/{project_id}/bundle",
        },
    }


_TAIL_BYTES = 4 * 1024 * 1024   # bytes read from the END of a log when tailing


def _tail_lines(path: Path, max_lines: int, max_bytes: int = _TAIL_BYTES) -> list[str]:
    """Read the last ``max_lines`` lines reading at most ``max_bytes`` from EOF.

    Bounds memory regardless of how large the log has grown (a single failing run
    can produce a multi-GB debug_trace.jsonl).
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()  # drop the partial first line
            raw = fh.read()
    except OSError:
        return []
    lines = raw.decode(ENC, errors="replace").splitlines()
    return lines[-max_lines:]


def _serve_log_tail(project_id: str, max_lines: int = 400, redact: bool = True) -> str:
    """Lines mentioning this project (whole-word) from the most-recent serve logs."""
    try:
        from onemancompany.core.config import DATA_ROOT
        log_dir = DATA_ROOT / "logs"
    except Exception:
        return ""
    if not log_dir.is_dir():
        return ""
    if not re.match(r"^[\w\-]{4,}$", project_id):
        return ""
    pat = re.compile(r"\b" + re.escape(project_id) + r"\b")
    files = sorted(log_dir.glob("omc_*.log"), reverse=True)[:3]
    hits: list[str] = []
    for f in files:
        # tail each log (bounded) then filter — never load the whole file
        for line in _tail_lines(f, max_lines * 4):
            if pat.search(line):
                hits.append(line)
    tail = hits[-max_lines:]
    return redact_text("\n".join(tail), redact)


def _read_text_capped(path: Path, cap: int = _MAX_INLINE_TEXT) -> tuple[str, bool]:
    """Read up to ``cap`` bytes of text. Returns (text, truncated)."""
    try:
        size = path.stat().st_size
    except OSError:
        return "", False
    with path.open("rb") as fh:
        raw = fh.read(cap)
    truncated = size > cap
    text = raw.decode(ENC, errors="replace")
    if truncated:
        text += f"\n\n…[truncated — file is {size:,} bytes, showing first {cap:,}]…\n"
    return text, truncated


def _is_textual(path: Path) -> bool:
    """Decide text vs binary by suffix, then content sniff."""
    suf = path.suffix.lower()
    if suf in _BINARY_MEDIA:
        return False
    if suf in TEXT_SUFFIXES:
        return True
    try:
        with path.open("rb") as fh:
            chunk = fh.read(_SNIFF_BYTES)
    except OSError:
        return False
    if b"\x00" in chunk:
        return False
    try:
        chunk.decode(ENC)
        return True
    except UnicodeDecodeError:
        return False


# Generic OMC tools — anything else an agent calls is a "specialized" talent tool.
_GENERIC_TOOLS = {
    "read", "write", "ls", "edit", "glob_files", "grep_search", "bash",
    "web_search", "load_skill", "list_colleagues", "image_generation",
    "run_debate", "select_debate_participants_tool", "sandbox_run_command",
    "self_assess", "report_to_ceo", "ask_user", "finish", "done", "think",
    "str_replace", "view", "create_file", "save_project_file", "message_colleague",
}
_AGENT_TRACE_CAP = 32 * 1024 * 1024   # bytes of debug_trace read for the agents view


def _agent_activity(pdir: Path) -> list[dict]:
    """Per-employee activity from debug_trace.jsonl: name/role/model, the tools
    actually called, and whether any specialized (non-generic) tool was used —
    so the UI can show which talent ran each stage and flag generic placeholder
    runs (declared talent tools never invoked)."""
    import json as _json
    trace = pdir / "debug_trace.jsonl"
    if not trace.exists() or trace.is_symlink():
        return []
    stage_by_talent: dict = {}
    try:
        from onemancompany.core.pipeline_engine import STAGE_TALENT_DEFAULTS
        stage_by_talent = {v: k for k, v in STAGE_TALENT_DEFAULTS.items()}
    except Exception:
        pass
    # talent_id -> declared specialized tools (from hire_list.json), so we can tell
    # "generic by design" apart from "declared tools but never used" (broken placeholder).
    declared_spec: dict = {}
    try:
        from onemancompany.core.config import SOURCE_ROOT
        for hp in (SOURCE_ROOT / "company" / "hire_list.json",
                   pdir.parents[5] / "hire_list.json" if len(pdir.parents) > 5 else None):
            if hp and hp.exists():
                hl = _json.loads(hp.read_text(encoding=ENC))
                items = hl if isinstance(hl, list) else hl.get("employees") or hl.get("hires") or []
                for e in items:
                    if isinstance(e, dict) and e.get("talent_id"):
                        decl = [t for t in (e.get("tools") or []) if t not in _GENERIC_TOOLS]
                        declared_spec[e["talent_id"]] = decl
                break
    except Exception as exc:  # pragma: no cover
        logger.debug("[debug] hire_list declared-tools load failed: {}", exc)
    try:
        with trace.open("rb") as fh:
            raw = fh.read(_AGENT_TRACE_CAP)
        lines = raw.decode(ENC, errors="replace").splitlines()
    except OSError:
        return []
    agg: dict = {}
    order: list = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            e = _json.loads(ln)
        except Exception:
            continue
        if not isinstance(e, dict):
            continue
        eid = str(e.get("employee_id", "") or "?")
        d = agg.get(eid)
        if d is None:
            d = {"models": set(), "calls": 0, "tools": {}, "available": 0}
            agg[eid] = d
            order.append(eid)
        d["calls"] += 1
        if e.get("model"):
            d["models"].add(str(e.get("model")))
        tlist = e.get("tools")
        if isinstance(tlist, list):
            d["available"] = max(d["available"], len(tlist))
        for m in (e.get("messages") or []):
            if not isinstance(m, dict):
                continue
            for tc in (m.get("tool_calls") or []):
                if not isinstance(tc, dict):
                    continue
                fn = (tc.get("function") or {}).get("name") or tc.get("name")
                if fn:
                    d["tools"][fn] = d["tools"].get(fn, 0) + 1
    try:
        from onemancompany.core.store import load_employee, load_ex_employees
        ex = None
        for eid, d in agg.items():
            emp = load_employee(eid)
            if not emp:
                ex = ex if ex is not None else load_ex_employees()
                emp = (ex or {}).get(eid, {}) if isinstance(ex, dict) else {}
            emp = emp or {}
            d["name"] = emp.get("name", "") or emp.get("nickname", "")
            d["role"] = emp.get("title", "") or emp.get("department", "")
            d["talent_id"] = emp.get("talent_id", "")
    except Exception as exc:  # pragma: no cover
        logger.debug("[debug] agent identity resolve failed: {}", exc)

    out = []
    for eid in order:
        d = agg[eid]
        spec = sorted(t for t in d["tools"] if t not in _GENERIC_TOOLS)
        tid = d.get("talent_id", "")
        decl = declared_spec.get(tid, [])
        # verdict: real (used specialized) / broken (declared but unused) / generic (none declared)
        if spec:
            verdict = "real"
        elif decl:
            verdict = "broken"     # declares specialized/MCP tools but called none
        else:
            verdict = "generic"    # prompt-only talent by design
        out.append({
            "employee_id": eid,
            "name": d.get("name", ""),
            "role": d.get("role", ""),
            "talent_id": tid,
            "stage": stage_by_talent.get(tid),
            "models": sorted(d["models"]),
            "calls": d["calls"],
            "tools_available": d["available"],
            "specialized_tools": spec,
            "declared_specialized": sorted(decl),
            "tools_called": d["tools"],
            "verdict": verdict,
            "is_placeholder": verdict == "broken",
        })
    out.sort(key=lambda a: (a["stage"] is None, a["stage"] if a["stage"] is not None else 99))
    return out


def _has_pdf(pdir: Path) -> bool:
    """Cheap PDF presence check — canonical location first, then a bounded walk."""
    if (pdir / "stage8_pdf" / "main.pdf").is_file():
        return True
    for i, p in enumerate(pdir.rglob("*.pdf")):
        try:
            if p.is_file() and not p.is_symlink():
                return True
        except OSError:
            pass
        if i >= 500:           # don't walk an unbounded tree for a missing pdf
            break
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────
@debug_router.get("/api/debug/runs")
async def debug_runs(request: Request, limit: int = 200, redact: int = 1) -> dict:
    """List every pipeline run with a light per-run debug summary."""
    from onemancompany.core.project_archive import (
        list_projects, load_named_project, load_iteration,
    )
    do_redact = _effective_redact(redact, request)
    limit = max(1, min(int(limit or 200), 1000))
    try:
        projects = list_projects()[:limit]
    except Exception as exc:   # one corrupt project record must not 500 the index
        logger.exception("[debug] list_projects failed: {}", exc)
        projects = []
    runs = []
    for proj in projects:
        if not isinstance(proj, dict):
            continue
        pid = proj.get("project_id", "")
        entry = {
            "project_id": pid,
            "name": redact_text(str(proj.get("name", pid)), do_redact),
            "task": redact_text(str(proj.get("task", "")), do_redact),
            "status": proj.get("status", ""),
            "latest_iter_status": proj.get("latest_iter_status", ""),
            "created_at": proj.get("created_at", ""),
            "iteration_count": proj.get("iteration_count", 0),
            "cost_usd": _safe_float(proj.get("cost_usd", 0.0)),
            "current_stage": None,
            "phase": None,
            "has_pdf": False,
            "tokens_total": 0,
        }
        try:
            pdir = _project_dir(pid)
            if pdir:
                state = _read_pipeline_state(pdir)
                entry["current_stage"] = state.get("current_stage")
                entry["phase"] = state.get("phase")
                entry["has_pdf"] = _has_pdf(pdir)
                named = load_named_project(pid)
                total = 0
                for it in (named.get("iterations", []) if named else []):
                    idoc = load_iteration(pid, it) or {}
                    cost = idoc.get("cost", {}) if isinstance(idoc, dict) else {}
                    tu = cost.get("token_usage", {}) if isinstance(cost, dict) else {}
                    total += _safe_int(tu.get("total"))
                entry["tokens_total"] = total
        except Exception as exc:  # pragma: no cover
            logger.debug("[debug] runs summary {} failed: {}", pid, exc)
        runs.append(entry)
    return {"runs": runs, "total": len(runs)}


@debug_router.get("/api/debug/run/{project_id}")
async def debug_run_info(project_id: str, request: Request, iteration: str = "",
                         redact: int = 1) -> JSONResponse:
    """Full per-run bundle: config, tokens, sandbox, artifacts, logs index."""
    from onemancompany.core.project_archive import (
        load_named_project, load_iteration, load_project,
    )
    pdir = _project_dir(project_id, iteration)
    if not pdir:
        return JSONResponse({"error": "run not found", "project_id": project_id},
                            status_code=404)
    do_redact = _effective_redact(redact, request)
    proj_doc = load_named_project(project_id)
    iter_doc = load_iteration(project_id, iteration) if iteration else None
    if iter_doc is None:
        iter_doc = load_project(project_id)
    try:
        info = build_run_info(project_id, iteration, pdir, proj_doc, iter_doc,
                              redact=do_redact)
    except Exception as exc:
        logger.exception("[debug] build_run_info failed for {}: {}", project_id, exc)
        return JSONResponse({"error": "failed to assemble run info",
                             "project_id": project_id}, status_code=500)
    return JSONResponse(_json_safe(info))   # scrub inf/nan → null (no 500)


@debug_router.get("/api/debug/run/{project_id}/files")
async def debug_run_files(project_id: str, request: Request, iteration: str = "",
                          internal: int = 1, redact: int = 1) -> JSONResponse:
    """Complete recursive file tree for a run (internal files included by default)."""
    pdir = _project_dir(project_id, iteration)
    if not pdir:
        return JSONResponse({"error": "run not found"}, status_code=404)
    do_redact = _effective_redact(redact, request)
    try:
        tree = build_file_tree(pdir, include_internal=bool(internal))
        if do_redact:                       # a secret can hide in a filename too
            for f in tree:
                f["path"] = redact_text(f["path"], True)
                f["name"] = redact_text(f["name"], True)
    except Exception as exc:
        logger.exception("[debug] file tree failed for {}: {}", project_id, exc)
        return JSONResponse({"error": "failed to list files"}, status_code=500)
    return JSONResponse({
        "project_id": project_id,
        "count": len(tree),
        "files": tree,
    })


@debug_router.get("/api/debug/run/{project_id}/agents")
async def debug_run_agents(project_id: str, iteration: str = "") -> JSONResponse:
    """Per-stage employee/talent activity: which talent ran, model, tools actually
    called, and whether it ran as a generic placeholder (no specialized tools)."""
    pdir = _project_dir(project_id, iteration)
    if not pdir:
        return JSONResponse({"error": "run not found"}, status_code=404)
    try:
        agents = _agent_activity(pdir)
    except Exception as exc:
        logger.exception("[debug] agents failed for {}: {}", project_id, exc)
        return JSONResponse({"error": "failed to assemble agents"}, status_code=500)
    return JSONResponse({"project_id": project_id, "agents": agents})


@debug_router.get("/api/debug/run/{project_id}/file")
async def debug_run_file(project_id: str, request: Request, path: str = Query(...),
                         iteration: str = "", redact: int = 1):
    """Download ANY artifact within a run.

    Text (incl. .html/.svg/.xml) is served as inert ``text/plain`` with redaction
    + ``nosniff`` + a locked CSP; images/PDF stream with their real media type.
    Credential files and anything escaping the run dir are refused.
    """
    pdir = _project_dir(project_id, iteration)
    if not pdir:
        return Response(content="run not found", status_code=404)
    target = _safe_target(pdir, path)
    if target is None:
        return Response(content="Forbidden", status_code=403)
    if _path_is_denied(path) or _is_denied(target.name):
        return Response(content="Forbidden (credential file)", status_code=403)
    if not target.is_file():
        return Response(content="Not found", status_code=404)

    do_redact = _effective_redact(redact, request)
    suffix = target.suffix.lower()

    # textual artifact carrying a PEM private key / dotenv secrets dump — refuse
    if suffix not in _BINARY_MEDIA and (
            _content_has_private_key(target) or _content_is_secret_dump(target)):
        return Response(content="Forbidden (contains credential material)",
                        status_code=403)

    if suffix in _BINARY_MEDIA:
        # NB: binary content (PDF/image metadata) cannot be text-redacted; flag it.
        disp = "inline" if suffix in _INLINE_BINARY else "attachment"
        return FileResponse(
            target, media_type=_BINARY_MEDIA[suffix],
            content_disposition_type=disp,
            headers={"X-Content-Type-Options": _NOSNIFF,
                     "X-Debug-Redaction": "not-applied-binary"},
        )

    if _is_textual(target):
        text, _ = _read_text_capped(target)
        text = redact_text(text, do_redact)
        return Response(
            content=text, media_type="text/plain; charset=utf-8",
            headers={"X-Content-Type-Options": _NOSNIFF,
                     "Content-Security-Policy": _TEXT_CSP},
        )

    # Unknown binary → stream as attachment, never inline.
    return FileResponse(
        target, media_type="application/octet-stream",
        content_disposition_type="attachment",
        headers={"X-Content-Type-Options": _NOSNIFF},
    )


@debug_router.get("/api/debug/run/{project_id}/logs")
async def debug_run_logs(project_id: str, request: Request, iteration: str = "",
                         tail: int = 400, redact: int = 1) -> JSONResponse:
    """Merged logs: the run's debug_trace.jsonl + matching serve-log tail."""
    pdir = _project_dir(project_id, iteration)
    if not pdir:
        return JSONResponse({"error": "run not found"}, status_code=404)
    do_redact = _effective_redact(redact, request)
    tail = max(1, min(int(tail or 400), _MAX_LOG_TAIL))

    trace = pdir / "debug_trace.jsonl"
    trace_lines: list[str] = []
    if trace.exists() and not trace.is_symlink():
        # tail from EOF — never materialise a multi-GB trace in memory
        trace_lines = [redact_text(ln, do_redact) for ln in _tail_lines(trace, tail)]

    return JSONResponse({
        "project_id": project_id,
        "debug_trace": {
            "present": trace.exists(),
            "lines_returned": len(trace_lines),
            "lines": trace_lines,
        },
        "serve_log_tail": _serve_log_tail(project_id, tail, do_redact),
    })


@debug_router.get("/api/debug/run/{project_id}/bundle")
async def debug_run_bundle(project_id: str, request: Request, iteration: str = "",
                           redact: int = 1):
    """Download EVERYTHING for a run as one zip (+ a debug_manifest.json).

    Text is redacted in-place (and skipped fail-closed if it can't be read);
    binaries are added verbatim. Credential files and symlinks are excluded.
    Bounded by file-count and total-bytes caps and spilled to a temp file so a
    single request can't exhaust server RAM; the manifest notes anything dropped.
    """
    import json as _json
    from urllib.parse import quote
    from onemancompany.core.project_archive import (
        load_named_project, load_iteration, load_project,
    )

    pdir = _project_dir(project_id, iteration)
    if not pdir:
        return Response(content="run not found", status_code=404)
    do_redact = _effective_redact(redact, request)

    proj_doc = load_named_project(project_id)
    iter_doc = (load_iteration(project_id, iteration) if iteration else None) or load_project(project_id)
    try:
        manifest = build_run_info(project_id, iteration, pdir, proj_doc, iter_doc,
                                  redact=do_redact)
    except Exception as exc:
        logger.exception("[debug] bundle manifest failed for {}: {}", project_id, exc)
        manifest = {"error": "manifest assembly failed", "project_id": project_id}
    manifest["_redacted"] = do_redact

    dropped: list[str] = []
    added = 0
    total = 0
    # Spill to a temp file (auto-deleted on close) — bounded memory regardless of size.
    spool = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)
    with zipfile.ZipFile(spool, "w", zipfile.ZIP_DEFLATED) as zf:
        for p, rel in _iter_files(pdir, include_internal=True):
            if added >= _BUNDLE_MAX_FILES or total >= _BUNDLE_MAX_BYTES:
                dropped.append(rel)
                continue
            # redact the arcname too (a secret can hide in a filename)
            arc = f"{project_id}/{redact_text(rel, True) if do_redact else rel}"
            try:
                sz = p.stat().st_size
            except OSError:
                continue
            if sz > _BUNDLE_MAX_MEMBER:        # skip oversized single members
                dropped.append(f"{rel} (>{_BUNDLE_MAX_MEMBER} bytes)")
                continue
            if do_redact and _is_textual(p):
                # fail CLOSED: if we can't read+redact, skip — never write raw.
                try:
                    text, _ = _read_text_capped(p, cap=_MAX_INLINE_TEXT)
                    zf.writestr(arc, redact_text(text, True))
                    added += 1
                    total += len(text)
                except Exception as exc:
                    logger.debug("[debug] bundle skip (redact failed) {}: {}", rel, exc)
                continue
            try:
                zf.write(p, arc)
                added += 1
                total += sz
            except OSError:
                continue
        manifest["_bundle"] = {"files_added": added, "approx_bytes": total,
                               "dropped_count": len(dropped), "dropped": dropped[:50]}
        # allow_nan=False + fallback so the manifest is always strict-valid JSON.
        try:
            man_json = _json.dumps(manifest, indent=2, default=str, allow_nan=False)
        except ValueError:
            man_json = _json.dumps({"error": "manifest had non-finite values",
                                    "project_id": project_id, "_redacted": do_redact},
                                   indent=2)
        zf.writestr("debug_manifest.json", man_json)
    spool.seek(0)

    fname = f"{project_id}_debug_bundle.zip"
    safe = fname.encode("ascii", "ignore").decode() or "debug_bundle.zip"
    enc = quote(fname, safe="")
    return StreamingResponse(
        spool, media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{safe}"; filename*=UTF-8\'\'{enc}',
            "X-Content-Type-Options": _NOSNIFF,
        },
    )
