"""Centralised credential / env-var coordination.

The agent calls :func:`request_env` when it needs one or more env vars
that aren't set yet. The manager:

  1. Writes a placeholder line to ``.env`` so the variable is visible
     in the ENV Management panel even before the user types anything.
  2. Publishes an :data:`EventType.ENV_REQUEST` event the frontend
     turns into a highlighted row.
  3. Awaits a per-key :class:`asyncio.Future` indefinitely — the user
     decides when to fill it in, no timeout.

When the user clicks Save in the ENV panel (or edits ``.env`` directly,
picked up by :func:`_on_env_file_changed`), :func:`save_env` writes the
value, updates :mod:`os.environ`, and resolves any matching futures.
Multiple agents requesting the same key share the same waiter list, so
one Save unblocks all of them.

This module is the single source of truth for credential delivery —
the older chat-based ``request_api_key`` / ``credential_request``
interaction path is removed in favour of it (issue #82 follow-up:
the chat path was easy to miss when the conversation panel was buried,
which is why the experiment runner stalled on infra creds in production).
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


# A line such as ``FOO_API_KEY=__OMC_PENDING__`` in ``.env`` is the
# "I need this — user hasn't filled it in yet" marker. The watcher and
# startup-restore both look for it.
PLACEHOLDER_VALUE = "__OMC_PENDING__"


@dataclass
class EnvVarRequest:
    """One pending env-var ask. Multiple agents can attach a Future for
    the same key — they all resolve when the value lands."""
    key: str
    label: str
    secret: bool
    requested_by: str
    reason: str
    future: asyncio.Future = field(repr=False)
    # The loop the future was created on — needed because the watchdog
    # callback that resolves these runs in a separate observer thread,
    # and ``Future.set_result`` is not thread-safe.
    loop: asyncio.AbstractEventLoop | None = field(default=None, repr=False)


# key -> list of pending futures (concurrent agents share the list).
_pending: dict[str, list[EnvVarRequest]] = {}

# Keys we've ever heard about (from .env on disk OR a request_env call).
# Used by ``list_env`` so the frontend can render the full row set.
_known_keys: set[str] = set()

_lock = asyncio.Lock()


def _env_path() -> Path:
    """Return the canonical .env path. Pulled into a function so tests
    can monkeypatch it without touching production config imports."""
    from onemancompany.core.config import DATA_ROOT, DOT_ENV_FILENAME
    return DATA_ROOT / DOT_ENV_FILENAME


def _read_env_file() -> dict[str, str]:
    path = _env_path()
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        out[k.strip()] = v.strip()
    return out


def _write_env_file(updates: dict[str, str]) -> None:
    """Add or update each ``key=value`` in ``.env``. Preserves
    surrounding comments and untouched lines."""
    path = _env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_lines: list[str] = []
    if path.exists():
        existing_lines = path.read_text(encoding="utf-8").splitlines()
    remaining = dict(updates)
    for i, line in enumerate(existing_lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        k = stripped.split("=", 1)[0].strip()
        if k in remaining:
            existing_lines[i] = f"{k}={remaining.pop(k)}"
    for k, v in remaining.items():
        existing_lines.append(f"{k}={v}")
    path.write_text("\n".join(existing_lines) + "\n", encoding="utf-8")


async def request_env(
    keys: list[dict],
    requested_by: str,
    reason: str,
) -> dict[str, str]:
    """Ask the user for one or more env vars. Blocks until every key
    has a non-placeholder value, then returns ``{key: value, ...}``.

    Each entry in ``keys`` is a dict with:
      - ``name`` (required): env var name (e.g. ``INFRA_SERVER_URL``).
      - ``label`` (optional): human label for the UI; defaults to ``name``.
      - ``secret`` (optional, bool): mask in the UI; defaults to True.

    If the value is already in ``os.environ`` (real, not placeholder),
    we return immediately for that key without prompting.
    """
    loop = asyncio.get_running_loop()
    result: dict[str, str] = {}
    new_requests: list[EnvVarRequest] = []

    on_disk = _read_env_file()
    for entry in keys:
        name = entry["name"]
        _known_keys.add(name)
        # Honor pre-filled values in either os.environ or .env so the user
        # can set the key before the agent ever runs and skip the prompt.
        existing = os.environ.get(name) or on_disk.get(name, "")
        if existing and existing != PLACEHOLDER_VALUE:
            result[name] = existing
            os.environ[name] = existing
            continue
        fut: asyncio.Future = loop.create_future()
        req = EnvVarRequest(
            key=name,
            label=entry.get("label", name),
            secret=bool(entry.get("secret", True)),
            requested_by=requested_by,
            reason=reason,
            future=fut,
            loop=loop,
        )
        _pending.setdefault(name, []).append(req)
        new_requests.append(req)

    if not new_requests:
        return result

    # Write placeholders so the row is visible in the panel even before
    # the user types anything. Skip ones the user already half-filled.
    to_write = {
        r.key: PLACEHOLDER_VALUE
        for r in new_requests
        if on_disk.get(r.key, PLACEHOLDER_VALUE) == PLACEHOLDER_VALUE
    }
    if to_write:
        _write_env_file(to_write)

    await _publish_request_event(new_requests, requested_by, reason)

    # Block forever — user decides when to save. No timeout by design.
    # If our caller is cancelled, prune our requests from _pending so a
    # later save_env doesn't try to resolve them and the row clears.
    try:
        values = await asyncio.gather(*(r.future for r in new_requests))
    except asyncio.CancelledError:
        for r in new_requests:
            waiters = _pending.get(r.key)
            if waiters and r in waiters:
                waiters.remove(r)
                if not waiters:
                    _pending.pop(r.key, None)
        raise
    for req, v in zip(new_requests, values):
        result[req.key] = v
    return result


async def _publish_request_event(
    new_requests: list[EnvVarRequest],
    requested_by: str,
    reason: str,
) -> None:
    from onemancompany.core.events import event_bus, CompanyEvent
    from onemancompany.core.models import EventType
    payload = {
        "keys": [
            {"name": r.key, "label": r.label, "secret": r.secret}
            for r in new_requests
        ],
        "requested_by": requested_by,
        "reason": reason,
    }
    await event_bus.publish(CompanyEvent(
        type=EventType.ENV_REQUEST,
        payload=payload,
    ))


def _validate_update(key: str, value: str) -> None:
    """Reject anything that would corrupt ``.env`` (newline injection),
    or values that are themselves the placeholder marker."""
    if not key or not key.strip():
        raise ValueError("env var key must be non-empty")
    if any(ch in key for ch in "\n\r=") or key.strip() != key:
        raise ValueError(f"invalid env var key: {key!r}")
    if "\n" in value or "\r" in value:
        raise ValueError(f"env var value for {key} contains newline")
    if value == PLACEHOLDER_VALUE:
        raise ValueError(f"refusing to save placeholder marker for {key}")


def save_env(updates: dict[str, str]) -> None:
    """Persist ``updates`` to ``.env`` + :mod:`os.environ`, then resolve
    any matching pending futures. Called by the HTTP route the ENV
    panel posts to, and by the .env watcher.

    Thread-safe: futures may have been created on the asyncio loop, but
    this function can be invoked from the watchdog observer thread —
    resolve via ``loop.call_soon_threadsafe`` to avoid corrupting future
    state."""
    if not updates:
        return
    for k, v in updates.items():
        _validate_update(k, v)
    _write_env_file(updates)
    for k, v in updates.items():
        os.environ[k] = v
        _known_keys.add(k)
        waiters = _pending.pop(k, [])
        for req in waiters:
            if req.future.done():
                continue
            target_loop = req.loop
            if target_loop and target_loop.is_running():
                target_loop.call_soon_threadsafe(_safe_set_result, req.future, v)
            else:
                req.future.set_result(v)


def _safe_set_result(fut: asyncio.Future, v: str) -> None:
    if not fut.done():
        fut.set_result(v)


def _on_env_file_changed() -> None:
    """Filesystem-watcher callback. Re-reads ``.env`` and resolves any
    pending futures whose key now has a real (non-placeholder) value."""
    on_disk = _read_env_file()
    resolved: dict[str, str] = {}
    for k, waiters in list(_pending.items()):
        v = on_disk.get(k)
        if v and v != PLACEHOLDER_VALUE:
            resolved[k] = v
    if resolved:
        save_env(resolved)


def scan_placeholders() -> list[str]:
    """Return keys whose value in ``.env`` is the placeholder. Used by
    the lifespan to re-emit ENV_REQUEST after a restart so the agent
    that was blocked before reboot resumes once the engine is up."""
    return [k for k, v in _read_env_file().items() if v == PLACEHOLDER_VALUE]


def list_env() -> list[dict]:
    """Snapshot for the ENV Management panel.

    Returns one row per known key. Real values are NEVER returned to
    the client — the panel only needs to know whether the value is set
    or pending. Secrets stay on the backend; the input box shows a
    placeholder for set rows."""
    on_disk = _read_env_file()
    names = set(on_disk) | set(_known_keys)
    rows: list[dict] = []
    for name in sorted(names):
        value = on_disk.get(name, os.environ.get(name, ""))
        pending = value == PLACEHOLDER_VALUE or name in _pending
        # Mark the current secret type so the frontend can decide
        # whether to render the input as password or text on edit.
        secret = True
        if name in _pending and _pending[name]:
            secret = _pending[name][0].secret
        rows.append({
            "name": name,
            "set": bool(value) and not pending,
            "pending": pending,
            "secret": secret,
        })
    return rows


def reset_for_tests() -> None:
    """Test-only: clear module state so fixtures isolate cleanly."""
    _pending.clear()
    _known_keys.clear()


# ---------------------------------------------------------------------------
# Filesystem watcher — wired from lifespan in main.py
# ---------------------------------------------------------------------------

_watcher_started = False


def start_env_watcher() -> None:
    """Install a ``watchdog`` observer on the .env directory. Idempotent."""
    global _watcher_started
    if _watcher_started:
        return
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        logger.warning("[env_manager] watchdog not installed; .env hot-reload disabled")
        return

    path = _env_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    class _Handler(FileSystemEventHandler):
        def on_modified(self, event):
            if Path(event.src_path).name == path.name:
                try:
                    _on_env_file_changed()
                except Exception as exc:
                    logger.warning("[env_manager] watcher callback raised: {}", exc)

    observer = Observer()
    observer.schedule(_Handler(), str(path.parent), recursive=False)
    observer.daemon = True
    observer.start()
    _watcher_started = True
    logger.info("[env_manager] .env watcher started at {}", path)


async def restore_pending_on_startup() -> None:
    """Re-emit ENV_REQUEST for placeholder rows so the user picks up
    where they left off after a restart."""
    pending = scan_placeholders()
    if not pending:
        return
    from onemancompany.core.events import event_bus, CompanyEvent
    from onemancompany.core.models import EventType
    await event_bus.publish(CompanyEvent(
        type=EventType.ENV_REQUEST,
        payload={
            "keys": [{"name": k, "label": k, "secret": True} for k in pending],
            "requested_by": "system",
            "reason": "Restored from previous session — please fill in.",
        },
    ))
