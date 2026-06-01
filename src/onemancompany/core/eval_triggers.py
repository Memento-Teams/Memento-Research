"""Stage-eval advisory triggers.

After each AutoResearch pipeline stage completes, dispatch a dedicated
``eval-agent`` employee to read the project workspace, build a
stage-specific checklist, and write an *advisory* evaluation report.

This is **advisory only**: it never gates the pipeline, never feeds back
into the producer → critic → gate loop, and a wrong verdict can only add a
report file — it can never block valid work. The design mirrors
``core/product_triggers.py``: subscribe once to the event bus and fan out
to handlers from a single queue.

Wiring:
  * The ``eval-agent`` talent (``company/hire_list.json``) carries the
    ``stage_eval`` skill; onboarding injects the ``stage-eval`` runbook.
  * The pipeline emits a ``stage_complete`` STATE_SNAPSHOT event
    (``pipeline_engine._emit_stage_event``) carrying ``project_id`` /
    ``project_dir`` / ``stage``.
  * ``register_eval_triggers()`` is started in ``main.py`` lifespan.
"""
from __future__ import annotations

import asyncio
import time

from loguru import logger

from onemancompany.core.config import (
    TASK_TREE_FILENAME,
    load_employee_configs,
    settings,
)
from onemancompany.core.events import event_bus
from onemancompany.core.models import EventType

# Skill key carried by the eval-agent talent (company/hire_list.json) and
# the default_skills/ runbook directory injected onto that employee.
EVAL_SKILL = "stage_eval"
EVAL_RUNBOOK = "stage-eval"

# Per-process dedup window. A replayed ``stage_complete`` (startup watchdog
# re-fire, frontend reconnect) within this window is ignored so we don't
# spawn a second report for the same stage. A genuine revert-and-rerun of a
# stage happens minutes apart and is correctly re-evaluated.
_DEDUP_WINDOW_SECONDS = 60.0
_recent_dispatch: dict[tuple[str, str], float] = {}


def _find_eval_employee() -> str | None:
    """Return the first employee whose skills include :data:`EVAL_SKILL`,
    or ``None`` if no eval-agent is on the roster."""
    for emp_id, cfg in load_employee_configs().items():
        if EVAL_SKILL in getattr(cfg, "skills", []):
            return emp_id
    return None


def _build_eval_prompt(stage_id: int, stage_name: str) -> str:
    """The dispatch instruction for one stage's advisory eval."""
    return (
        f"You are the Stage Eval Agent. Stage {stage_id} "
        f"({stage_name or '?'}) just completed in this project.\n\n"
        "## REQUIRED FIRST STEP\n"
        f'Call load_skill("{EVAL_RUNBOOK}") and follow that runbook for '
        f"Stage {stage_id}.\n\n"
        f"Then: read the project workspace, build the Stage {stage_id} "
        "checklist, verify each item against real evidence in the workspace "
        "(cite file:line), and WRITE your findings to "
        f"`stage{stage_id}_eval_report.md` in the project root.\n\n"
        "This report is ADVISORY ONLY — it does not gate the pipeline and is "
        "for the researcher's reference. Do NOT modify any deliverable; only "
        "write your own report file."
    )


def _dispatch_eval_for_stage(
    project_id: str, project_dir: str, stage_id: int, stage_name: str
) -> str | None:
    """Add an advisory eval task for ``eval-agent`` to the project task tree
    and schedule it.

    Returns the new node id, or ``None`` if no eval-agent is on the roster or
    the task tree has no root yet. Mirrors
    ``pipeline_engine._dispatch_to_employee`` but never mutates pipeline
    state — this is a side-channel advisory task.
    """
    from pathlib import Path

    from onemancompany.core.agent_loop import employee_manager
    from onemancompany.core.task_tree import get_tree, save_tree_async

    emp_id = _find_eval_employee()
    if not emp_id:
        logger.info(
            "[EVAL_TRIGGER] No eval-agent on roster; skipping Stage {} eval",
            stage_id,
        )
        return None

    tree_path = str(Path(project_dir) / TASK_TREE_FILENAME)
    tree = get_tree(Path(tree_path), project_id=project_id)
    if not tree.root_id:
        logger.debug(
            "[EVAL_TRIGGER] No task tree root for {}; skip Stage {}",
            project_id, stage_id,
        )
        return None

    # Parent under the EA node when present (same convention as the pipeline
    # dispatch), else the tree root.
    parent_id = tree.root_id
    root = tree.get_node(tree.root_id)
    if root:
        for child in tree.get_active_children(root.id):
            if child.employee_id in ("00004", "00002"):
                parent_id = child.id
                break

    node = tree.add_child(
        parent_id=parent_id,
        employee_id=emp_id,
        description=_build_eval_prompt(stage_id, stage_name),
        acceptance_criteria=[],
        title=f"Stage {stage_id} eval report",
    )
    node.project_id = project_id
    node.project_dir = project_dir
    node.metadata = {
        **(getattr(node, "metadata", None) or {}),
        "stage_eval_advisory": True,
        "stage": stage_id,
    }
    save_tree_async(tree_path)

    # Fresh conversation per eval task — same rationale as the pipeline
    # critic dispatch (a resumed cross-stage history blows the context
    # window). Best-effort; never block dispatch.
    try:
        from onemancompany.core.claude_session import reset_session

        reset_session(emp_id, project_id)
    except Exception as exc:
        logger.debug("[EVAL_TRIGGER] reset_session skipped for {}: {}", emp_id, exc)

    employee_manager.schedule_node(emp_id, node.id, tree_path)
    employee_manager._schedule_next(emp_id)
    logger.info(
        "[EVAL_TRIGGER] Dispatched Stage {} advisory eval to {} (node {})",
        stage_id, emp_id, node.id,
    )
    return node.id


async def handle_stage_complete(event) -> bool:
    """Maybe dispatch an advisory eval for a just-completed pipeline stage.

    Returns ``True`` iff an eval task was dispatched. No-ops (``False``) for
    non-pipeline events, missing payload fields, a stage already evaluated
    within the dedup window, or when no eval-agent is on the roster.
    """
    payload = getattr(event, "payload", None) or {}
    if not payload.get("pipeline_managed"):
        return False

    project_id = payload.get("project_id")
    project_dir = payload.get("project_dir")
    stage_id = payload.get("stage")
    if not (project_id and project_dir and stage_id is not None):
        logger.debug(
            "[EVAL_TRIGGER] stage_complete missing project_id/project_dir/stage; skip"
        )
        return False

    key = (str(project_id), str(stage_id))
    now = time.monotonic()
    last = _recent_dispatch.get(key)
    if last is not None and (now - last) < _DEDUP_WINDOW_SECONDS:
        return False

    node_id = _dispatch_eval_for_stage(
        project_id, project_dir, int(stage_id), payload.get("stage_name", "")
    )
    if node_id:
        _recent_dispatch[key] = now
        return True
    return False


def register_eval_triggers() -> "asyncio.Task | None":
    """Subscribe an advisory eval dispatcher to the event bus.

    Returns the ``asyncio.Task`` (so the caller can cancel it on shutdown),
    or ``None`` when stage-eval is disabled via ``STAGE_EVAL_ENABLED=false``.
    """
    if not settings.stage_eval_enabled:
        logger.info(
            "[EVAL_TRIGGER] Stage-eval disabled (STAGE_EVAL_ENABLED=false)"
        )
        return None

    queue = event_bus.subscribe()

    async def _dispatch_loop() -> None:
        while True:
            event = await queue.get()
            try:
                if event.type == EventType.STATE_SNAPSHOT and (
                    getattr(event, "payload", None) or {}
                ).get("type") == "stage_complete":
                    await handle_stage_complete(event)
            except Exception:
                logger.exception(
                    "[EVAL_TRIGGER] Error handling event {}",
                    getattr(event, "type", "?"),
                )

    task = asyncio.ensure_future(_dispatch_loop())
    logger.info("[EVAL_TRIGGER] Stage-eval triggers registered")
    return task
