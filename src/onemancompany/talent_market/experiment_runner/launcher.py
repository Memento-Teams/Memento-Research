"""Subprocess launcher with Unix process-group cleanup."""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Callable
from pathlib import Path

from onemancompany.talent_market.experiment_runner.models import (
    ExperimentConfig,
    ProcessHandle,
)

TAIL_LIMIT = 80


async def launch_experiment(
    config: ExperimentConfig,
    on_log: Callable[[str, str], None] | None = None,
) -> ProcessHandle:
    """Launch the user command without rewriting it."""

    cwd = Path(config.working_dir).expanduser() if config.working_dir else Path.cwd()
    env = {**os.environ, **config.extra_env}
    if config.metric_dir:
        env.setdefault("EXPERIMENT_METRIC_DIR", str(Path(config.metric_dir).expanduser()))

    process = await asyncio.create_subprocess_shell(
        config.command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
        env=env,
        preexec_fn=os.setsid if hasattr(os, "setsid") else None,
    )
    handle = ProcessHandle(pid=process.pid, process=process, command=config.command)
    if on_log:
        on_log("start", f"Started experiment PID={process.pid}")

    handle.reader_tasks = [
        asyncio.create_task(_read_stream(process.stdout, handle.stdout_tail, "stdout", on_log)),
        asyncio.create_task(_read_stream(process.stderr, handle.stderr_tail, "stderr", on_log)),
    ]
    return handle


async def terminate_process_group(
    handle: ProcessHandle,
    reason: str,
    on_log: Callable[[str, str], None] | None = None,
    grace_seconds: float = 10.0,
) -> None:
    """Two-stage kill: SIGTERM for checkpoint cleanup, SIGKILL fallback."""

    proc = handle.process
    if proc.returncode is not None:
        return

    if on_log:
        on_log("kill", f"Stopping process group for PID={handle.pid}: {reason}")

    try:
        pgid = os.getpgid(handle.pid)
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:
        proc.terminate()

    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
        return
    except asyncio.TimeoutError:
        pass

    try:
        pgid = os.getpgid(handle.pid)
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except Exception:
        proc.kill()
    await proc.wait()


async def wait_for_readers(handle: ProcessHandle) -> None:
    if handle.reader_tasks:
        await asyncio.gather(*handle.reader_tasks, return_exceptions=True)


async def _read_stream(
    stream: asyncio.StreamReader | None,
    tail: list[str],
    log_type: str,
    on_log: Callable[[str, str], None] | None,
) -> None:
    if stream is None:
        return
    while True:
        line = await stream.readline()
        if not line:
            return
        text = line.decode("utf-8", errors="replace").rstrip()
        if text:
            tail.append(text)
            if len(tail) > TAIL_LIMIT:
                del tail[:-TAIL_LIMIT]
            if on_log:
                on_log(log_type, text)
