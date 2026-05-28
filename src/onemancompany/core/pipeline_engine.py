"""
pipeline_engine.py — Deterministic state machine for the 9-stage research pipeline.

Replaces LLM-driven orchestration (EA/Research Director reading SOP).
The pipeline engine controls stage sequencing, critic dispatch, and CEO gates.
LLM agents only do research work within a stage — they never decide "what's next."

Runs on top of OMC: uses employee_manager.schedule_node() to dispatch tasks,
task tree for node management, and WebSocket events for frontend updates.
"""

from __future__ import annotations

import re
import yaml
from pathlib import Path
from loguru import logger

from onemancompany.core.events import event_bus, CompanyEvent, EventType
from onemancompany.core.config import SYSTEM_AGENT
from onemancompany.core.config import load_employee_configs
from onemancompany.core.research_memory import ResearchMemoryStore

# ---------------------------------------------------------------------------
# Stage definitions
# ---------------------------------------------------------------------------

STAGES = [
    {"id": 1, "skill": "topic_refiner",        "name": "Topic Refinement"},
    {"id": 2, "skill": "literature_surveyor",   "name": "Literature Survey"},
    {"id": 3, "skill": "idea_generator",        "name": "Idea Generation"},
    {"id": 4, "skill": "methodology_designer",  "name": "Methodology Design"},
    {"id": 5, "skill": "experiment_designer",   "name": "Experiment Design"},
    {"id": 6, "skill": "experimentalist",       "name": "Auto Experiment"},
    {"id": 7, "skill": "result_analyst",        "name": "Result Analysis"},
    {"id": 8, "skill": "paper_writer",          "name": "Paper Generation"},
    {"id": 9, "skill": "peer_reviewer",         "name": "Self-Review"},
]

CRITIC_SKILL = "adversarial_review"
MAX_RETRIES = 3
# Stage 6's code-fix loop (impl_critic rejects + smoke-failure route-backs) each
# surface a real, distinct bug that the code-writer then fixes — productive
# progress, not spinning — so it gets more headroom than the generic budget.
# (A canary smoke-fail + a JSON-bug smoke-fail used to exhaust 3 retries before
# the now-fixed code ever got to run the full experiment.)
STAGE6_IMPL_MAX_RETRIES = 6

# Iteration identifier used in git tag names (``<iteration>/stage-<N>``).
# The literal directory name (e.g. ``iter_001``) is fine — git tag names
# allow underscores. Centralised here so the engine and project_repo
# agree on tag format.
_DEFAULT_ITERATION = "iter_001"


class RevertNotAllowedError(Exception):
    """Raised when ``revert_to_stage`` is called in a phase that would
    clobber in-flight work. Only ``gate`` and ``done`` are safe."""

# Tag for pipeline-managed nodes so vessel can identify them
PIPELINE_NODE_TAG = "pipeline_managed"


def _recover_verdict_from_gate_review(project_dir: str, stage_id: int) -> str | None:
    """When a critic's submit_result is a stub placeholder (the executor's
    "Executed: write" capture), recover the real verdict from the
    ``stage{N}_gate_review*.md`` file the critic actually wrote.

    Most critic skills `write()` their full verdict to a versioned
    review file and only echo a short summary via `submit_result()`.
    If the agent loop terminates before the summary is submitted, the
    engine captures the stub — but the verdict is intact on disk. This
    helper recovers it so a real PASS isn't auto-REJECTed.

    Returns the file's contents (str) on hit, or None if no review file
    was found. Picks the most recently modified file when multiple
    versions exist (e.g. ``stage6_gate_review_v3.md``).
    """
    try:
        candidates = list(Path(project_dir).glob(f"stage{stage_id}*gate_review*.md"))
    except Exception:
        return None
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    try:
        return candidates[0].read_text(encoding="utf-8")
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("[PIPELINE] failed to read recovered gate review {}: {}", candidates[0], exc)
        return None


def _detect_smoke_failure(result: str) -> str | None:
    """If the Stage 6b runner reports that the smoke run failed (a real
    implementation bug surfaced before committing the full experiment),
    return a short reason string. Otherwise None.

    Triggered phrases (case-insensitive):
      - ``SMOKE_FAIL``, ``SMOKE_TIMEOUT``         (literal markers our runbook emits)
      - ``blocked_smoke_failure`` / ``blocked_smoke_invalid``  (status markers)
      - ``QUALITY_FAIL_ACCURACY_ZERO`` / ``QUALITY_FAIL_TRUNCATED``  (quality-gate markers)
      - ``NO_RESULT_JSON`` / ``NO_METRICS``        (smoke RESULT_JSON unparseable)
      - ``smoke (test|run) ... failed``
      - ``smoke test failed``
      - ``smoke run ... status: failed``

    Why this matters: when the smoke caught a bug, routing the runner back
    to retry won't help — the bug is in the Stage 6a implementation. The
    engine should redispatch impl_producer (the code writer) with the
    smoke log_tail attached as feedback so the next code revision can
    target the actual error.
    """
    if not isinstance(result, str) or not result:
        return None
    low = result.lower()
    import re
    # Explicit markers from the runbook template. Order matters: check
    # longer / more specific markers first so the substring containment
    # check returns the most precise label (otherwise the shorter
    # "smoke_fail" would shadow the more specific "smoke_failure" /
    # "blocked_smoke_failure" matches).
    for marker in (
        "blocked_smoke_failure",
        "blocked_smoke_invalid",
        "quality_fail_accuracy_zero",
        "quality_fail_truncated",
        "no_result_json",
        "no_metrics",
        "smoke_timeout",
        "smoke_fail",
    ):
        if marker in low:
            return marker
    # Narrative phrasing
    if re.search(r"smoke\s+(?:test|run).{0,40}fail", low):
        return "smoke_failed_narrative"

    # Server-side smoke quality validation (LLM-bypass-proof). Even if the
    # runner agent decided to skip its own quality check and submit the
    # full run anyway, the engine scans the producer report for embedded
    # smoke RESULT_JSON evidence and rejects garbage scientifically:
    #
    #   - accuracy fields all zero (no condition got any right)
    #   - truncation indicators ≥ 50% of n (model never stopped cleanly)
    #
    # We accept BOTH common naming conventions because implementations
    # have shipped both forms:
    #   "accuracy_direct": 0.0      and      "direct_accuracy": 0.0
    #   "direct_truncated": 3, n=3  and      "direct_truncation_rate": 1.0
    # Order: the field name may have the condition prefix OR suffix,
    # and truncation may be a count or a rate.
    accuracy_pat = r'"(?:accuracy_\w+|\w+_accuracy)"\s*:\s*([0-9]*\.?[0-9]+)[,\s}]'
    acc_hits = [float(x) for x in re.findall(accuracy_pat, result)]
    if acc_hits and len(acc_hits) >= 2 and all(v == 0.0 for v in acc_hits):
        return "quality_fail_accuracy_zero"

    # Truncation: try integer-count form first, then rate form.
    trunc_count = re.search(
        r'"(?:direct|a|control)_truncated"\s*:\s*(\d+).*?'
        r'"(?:cot|b|treatment)_truncated"\s*:\s*(\d+).*?'
        r'"n_problems"\s*:\s*(\d+)',
        result, re.DOTALL,
    )
    if trunc_count:
        t1, t2, n = (int(x) for x in trunc_count.groups())
        if n > 0 and (t1 + t2) >= n:  # ≥ 50% per condition
            return "quality_fail_truncated"
    trunc_rates = re.findall(
        r'"(?:[a-z]+_truncation_rate|truncation_rate_\w+)"\s*:\s*([0-9]*\.?[0-9]+)',
        result,
    )
    if trunc_rates and len(trunc_rates) >= 2:
        rs = [float(x) for x in trunc_rates]
        if all(r >= 0.5 for r in rs):
            return "quality_fail_truncated"

    return None


def _is_stub_producer_result(result: str) -> bool:
    """True if the producer's submitted result looks like a placeholder
    inserted by the executor when an agent terminated without calling
    ``submit_result``.

    Background: the LangChain executor wraps the agent's final state. When
    the agent runs out of thoughts and ends naturally, the executor often
    captures the description of the LAST tool call (e.g. ``"Executed: bash"``,
    ``"Executed: write"``) as the result string. That string is then stored
    by the engine as the stage's official output. The critic then either:
      (a) reads the stub and correctly REJECTs (good path), or
      (b) reads the stub and itself produces a stub (verdict-keyword-free
          critic result) which the engine's fallback parser misroutes.

    Letting (b) happen costs ~1 minute per round and wastes one of the
    bounded retry slots. Cheaper to short-circuit here: if the result
    looks like a stub, force REJECT + retry directly, attaching a
    feedback string that names the specific failure mode so the LLM's
    next attempt knows what it skipped.
    """
    if not isinstance(result, str):
        return True
    s = result.strip()
    if len(s) < 100:
        return True
    # Common literal placeholders the executor produces. Lowercased
    # contains-check so capitalisation variants still trip.
    low = s.lower()
    return (
        low.startswith("executed:")
        or low in {"task completed", "done.", "ok", "no more actions"}
    )

# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

STATE_FILENAME = "pipeline_state.yaml"


def _load_state(project_dir: str) -> dict:
    path = Path(project_dir) / STATE_FILENAME
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _save_state(project_dir: str, state: dict):
    path = Path(project_dir) / STATE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(state, f, default_flow_style=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Employee lookup
# ---------------------------------------------------------------------------

def _find_employee_by_skill(skill: str) -> str | None:
    """Find the first employee whose skills list contains the given skill."""
    configs = load_employee_configs()
    for emp_id, cfg in configs.items():
        if skill in cfg.skills:
            return emp_id
    return None


def _find_employee_for_stage(stage_id: int, primary_skill: str) -> str | None:
    """Resolve the producer employee for a stage with stage-specific fallbacks.

    Stage 6 (Auto Experiment) prefers an `experiment_runner` employee — they
    carry the `experiment-infra` runbook and can actually drive remote infra.
    If no runner is on the roster, fall back to `experimentalist` (the
    default research talent), who can still produce a simulated report.
    """
    if stage_id == 6:
        runner = _find_employee_by_skill("experiment_runner")
        if runner:
            return runner
    return _find_employee_by_skill(primary_skill)


def _find_employee_for_stage6_subphase(subphase: str) -> str | None:
    """Stage 6 has two sub-phases with different skill requirements.

    - ``impl_producer``: hand to the ``code_implementer`` who can write
      runnable Python from the Stage 5 prose plan and push it remote.
      No fallback — if the roster has no code_implementer, fail loudly
      so the operator notices the missing hire.
    - ``exec_producer``: hand to ``experiment_runner`` (preferred — owns
      the remote-infra scripts) or fall back to ``experimentalist``.
    """
    if subphase == "impl_producer":
        return _find_employee_by_skill("code_implementer")
    if subphase == "exec_producer":
        runner = _find_employee_by_skill("experiment_runner")
        if runner:
            return runner
        return _find_employee_by_skill("experimentalist")
    return None


# ---------------------------------------------------------------------------
# In-memory registry of active pipelines
# ---------------------------------------------------------------------------

_active_pipelines: dict[str, "PipelineEngine"] = {}  # project_id → engine


def get_pipeline(project_id: str) -> "PipelineEngine | None":
    return _active_pipelines.get(project_id)


def get_or_load_pipeline(project_id: str, project_dir: str) -> "PipelineEngine | None":
    """Get from memory or reload from disk state."""
    if project_id in _active_pipelines:
        return _active_pipelines[project_id]
    state = _load_state(project_dir)
    if not state:
        return None
    engine = PipelineEngine(project_id, project_dir, state.get("topic", ""))
    engine.state = state
    engine._ensure_memory_state()
    _active_pipelines[project_id] = engine
    return engine


# ---------------------------------------------------------------------------
# Pipeline Engine
# ---------------------------------------------------------------------------

class PipelineEngine:
    """Deterministic state machine for the research pipeline.

    Phases per stage:
        producer → critic → gate → (next stage or done)

    The engine dispatches tasks via OMC's task tree + employee_manager.
    It never calls an LLM itself.
    """

    def __init__(self, project_id: str, project_dir: str, topic: str):
        self.project_id = project_id
        self.project_dir = project_dir
        self.topic = topic
        self.state: dict = {
            "topic": topic,
            "current_stage": 1,
            "start_stage": 1,
            "end_stage": 9,
            "prior_context": "",
            "stage_assignments": {},  # stage_id (str) → employee_id override
            "phase": "producer",  # producer | critic | gate | done | failed
                                   # Stage 6 additionally uses sub-phases:
                                   # impl_producer | impl_critic | exec_producer | exec_critic
            "retries": 0,
            # Stage 6 keeps separate retry counters for the implementation
            # and execution sub-phases so a flaky runner doesn't burn the
            # impl retries (and vice versa).
            "impl_retries": 0,
            "exec_retries": 0,
            "stage_results": {},
            "critic_result": None,
            "active_node_id": None,  # current task node being executed
            "active_employee_id": None,
            "memory_retrievals": {},
            "memory_episodes": {},
            "memory_feedback": {},
        }
        _active_pipelines[project_id] = self

    @property
    def current_stage(self) -> int:
        return self.state.get("current_stage", 1)

    @property
    def phase(self) -> str:
        return self.state.get("phase", "producer")

    def _save(self):
        self._ensure_memory_state()
        _save_state(self.project_dir, self.state)

    def _ensure_memory_state(self):
        self.state.setdefault("memory_retrievals", {})
        self.state.setdefault("memory_episodes", {})
        self.state.setdefault("memory_feedback", {})

    def _stage_def(self, stage_id: int = None) -> dict:
        sid = stage_id or self.current_stage
        return STAGES[sid - 1] if 1 <= sid <= 9 else {}

    def _memory_store(self) -> ResearchMemoryStore:
        return ResearchMemoryStore(self.project_id, self.project_dir)

    # ------------------------------------------------------------------
    # Dispatch helpers
    # ------------------------------------------------------------------

    def _dispatch_to_employee(self, employee_id: str, description: str, title: str):
        """Create a task node in the tree and schedule it for the employee."""
        from onemancompany.core.task_tree import get_tree, save_tree_async
        from onemancompany.core.config import TASK_TREE_FILENAME
        from onemancompany.core.agent_loop import employee_manager

        tree_path = str(Path(self.project_dir) / TASK_TREE_FILENAME)
        tree = get_tree(Path(tree_path), project_id=self.project_id)

        # Find parent node (the root or EA node)
        root = tree.get_node(tree.root_id) if tree.root_id else None
        parent_id = tree.root_id
        # If root has an EA child, use that as parent
        if root:
            for child in tree.get_active_children(root.id):
                if child.employee_id in ("00004", "00002"):
                    parent_id = child.id
                    break

        node = tree.add_child(
            parent_id=parent_id,
            employee_id=employee_id,
            description=description,
            acceptance_criteria=[],
        )
        node.title = title
        node.project_id = self.project_id
        node.project_dir = self.project_dir
        # Tag so vessel knows this is pipeline-managed
        if not hasattr(node, 'metadata'):
            node.metadata = {}
        node.metadata = {**(node.metadata or {}), "pipeline_managed": True}

        save_tree_async(tree_path)

        self.state["active_node_id"] = node.id
        self.state["active_employee_id"] = employee_id
        self._save()

        # Start each pipeline stage with a FRESH Claude conversation. The
        # daemon otherwise resumes one session per (employee, project) and
        # accumulates history across every stage it touches — the critic
        # reviews all 9 stages, so its resumed history blows past the model
        # context window (observed: 623K tokens > 262K limit, Stage 6 critic
        # failed → empty deliverable). Pipeline tasks pass full context in
        # the prompt, so resumed history is pure overhead.
        try:
            from onemancompany.core.claude_session import reset_session
            reset_session(employee_id, self.project_id)
        except Exception as _e:  # best-effort; never block dispatch
            logger.debug("[PIPELINE] reset_session skipped for {}: {}", employee_id, _e)

        employee_manager.schedule_node(employee_id, node.id, tree_path)
        employee_manager._schedule_next(employee_id)

        logger.info(
            "[PIPELINE] Dispatched {} to employee {} (stage={}, phase={})",
            title, employee_id, self.current_stage, self.phase,
        )

    def _build_context(self) -> str:
        """Build cumulative context from prior context + all previous stage results.

        Stage result keys can be either plain stage numbers ("4", "5") or
        sub-phase keys for Stage 6 ("6_impl", "6_exec"). Sort by the
        numeric prefix and resolve the stage definition off that prefix
        so sub-phase keys don't crash ``int()``.
        """
        parts = [f"Research topic: {self.topic}\n"]
        prior = self.state.get("prior_context", "")
        if prior:
            parts.append(f"--- Prior Context (uploaded files) ---\n{prior}\n")

        def _base_sid(key: str) -> int:
            # "6" → 6; "6_impl" → 6; "6_exec" → 6
            return int(str(key).split("_", 1)[0])

        for sid in sorted(self.state.get("stage_results", {}).keys(),
                          key=lambda k: (_base_sid(k), str(k))):
            stage_def = self._stage_def(_base_sid(sid))
            result = self.state["stage_results"][sid]
            parts.append(f"--- Stage {sid}: {stage_def.get('name', '')} ---\n{result}\n")
        return "\n".join(parts)

    def _retrieve_memory_guidance(self, stage: dict, context: str, feedback: str = "") -> str:
        """Retrieve MemRL-style prior lessons for the current stage."""
        try:
            retrieved = self._memory_store().retrieve_stage_guidance(
                topic=self.topic,
                stage=stage,
                context=context,
                feedback=feedback,
            )
        except Exception as exc:
            logger.warning("[PIPELINE] Research memory retrieval failed: {}", exc)
            return ""

        self._ensure_memory_state()
        self.state["memory_retrievals"][str(stage["id"])] = {
            "ids": retrieved.memory_ids,
            "query": retrieved.query,
            "simmax": retrieved.simmax,
        }
        if retrieved.memory_ids:
            logger.info(
                "[PIPELINE] Retrieved {} research memories for stage {}",
                len(retrieved.memory_ids), stage["id"],
            )
        return retrieved.guidance

    def _record_stage_memory(
        self,
        stage: dict,
        *,
        producer_result: str,
        critic_result: str,
        passed: bool,
        confidence: float | None,
        outcome: str,
    ) -> str | None:
        self._ensure_memory_state()
        stage_key = str(stage["id"])
        retrieved_ids = self.state.get("memory_retrievals", {}).get(stage_key, {}).get("ids", [])
        reward = self._critic_reward(
            passed=passed,
            confidence=confidence,
            retries=self.state.get("retries", 0),
            exhausted=outcome == "critic_reject_exhausted",
        )
        try:
            memory_id = self._memory_store().record_stage_episode(
                topic=self.topic,
                stage=stage,
                producer_result=producer_result,
                critic_result=critic_result,
                passed=passed,
                confidence=confidence,
                retries=self.state.get("retries", 0),
                reward=reward,
                retrieved_memory_ids=retrieved_ids,
                outcome=outcome,
            )
        except Exception as exc:
            logger.warning("[PIPELINE] Research memory write failed: {}", exc)
            return None

        self.state["memory_episodes"][stage_key] = memory_id
        logger.info(
            "[PIPELINE] Recorded research memory {} for stage {} (reward={:.2f})",
            memory_id, stage["id"], reward,
        )
        return memory_id

    def _apply_ceo_memory_feedback(self, stage: dict, feedback: str, approved: bool) -> None:
        self._ensure_memory_state()
        stage_key = str(stage["id"])
        episode_id = self.state.get("memory_episodes", {}).get(stage_key)
        retrieved_ids = self.state.get("memory_retrievals", {}).get(stage_key, {}).get("ids", [])
        if not episode_id and not retrieved_ids:
            return
        try:
            update = self._memory_store().apply_ceo_feedback(
                episode_id=episode_id,
                retrieved_memory_ids=retrieved_ids,
                feedback=feedback,
                approved=approved,
            )
        except Exception as exc:
            logger.warning("[PIPELINE] Research memory CEO feedback update failed: {}", exc)
            return
        self.state["memory_feedback"][stage_key] = update
        self._save()

    @staticmethod
    def _critic_reward(
        *,
        passed: bool,
        confidence: float | None,
        retries: int,
        exhausted: bool = False,
    ) -> float:
        if exhausted:
            return -1.0
        if passed:
            base = confidence if confidence is not None else 0.7
            return max(-1.0, min(1.0, float(base) - (0.15 * int(retries))))
        miss = 1.0 - float(confidence if confidence is not None else 0.0)
        return max(-1.0, min(1.0, -max(0.35, miss)))

    # ------------------------------------------------------------------
    # Public API — called by routes.py and vessel.py
    # ------------------------------------------------------------------

    def _iteration_id(self) -> str:
        """Identifier used in git tag names. Standard layout is
        ``.../iterations/iter_NNN``; we use the basename directly so
        multi-iteration projects keep their tag namespaces separate.

        For legacy / non-standard layouts where the basename doesn't
        match ``iter_\\d+``, we hash the full project_dir into a stable
        synthetic id to avoid cross-iteration tag collisions (which
        would silently overwrite each other under ``tag -f``).
        """
        name = Path(self.project_dir).name
        if name and re.match(r"^iter_\d+$", name):
            return name
        if not name:
            return _DEFAULT_ITERATION
        # Non-standard dir name. Derive a stable synthetic id from the
        # path so different projects with the same basename don't collide.
        import hashlib
        digest = hashlib.sha1(self.project_dir.encode("utf-8")).hexdigest()[:8]
        logger.debug(
            "[PIPELINE] Non-standard project dir basename {!r}; using synthetic iteration id iter_{}",
            name, digest,
        )
        return f"iter_{digest}"

    def start(self, start_stage: int = 1, end_stage: int = 9, prior_context: str = "", stage_assignments: dict = None, auto_approve: bool = False):
        """Begin the pipeline from the given stage.

        ``auto_approve`` (headless/unattended mode): when True, every CEO gate
        is advanced automatically — the pipeline runs end-to-end with no human
        confirmation. Used for background full-auto runs.
        """
        self.state["current_stage"] = max(1, min(start_stage, 9))
        self.state["start_stage"] = self.state["current_stage"]
        self.state["end_stage"] = max(self.state["current_stage"], min(end_stage, 9))
        self.state["prior_context"] = prior_context
        self.state["stage_assignments"] = stage_assignments or {}
        self.state["auto_approve"] = bool(auto_approve)
        self.state["phase"] = "producer"
        self.state["retries"] = 0
        self.state["impl_retries"] = 0
        self.state["exec_retries"] = 0
        self._save()
        # Auto-init the workspace as a git repo so per-stage commits and
        # later revert-to-here ops have somewhere to land. Idempotent —
        # existing repos are left alone.
        from onemancompany.core import project_repo
        try:
            project_repo.ensure_initialized(self.project_dir, iteration=self._iteration_id())
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("[PIPELINE] project_repo init failed for {}: {}", self.project_dir, exc)
        logger.info("[PIPELINE] Starting from stage {} to stage {}", self.state["current_stage"], self.state["end_stage"])
        self._dispatch_producer()

    def queue_pending_feedback(self, text: str) -> None:
        """Buffer CEO/user feedback to inject into the next producer dispatch.

        Called when the CEO sends a chat message while the pipeline is mid-flight
        (producer/critic running, or auto-retrying after a REJECT). The pipeline
        is not at a gate, so we cannot call ``on_ceo_approve`` — but the user's
        guidance is valuable for the next producer iteration. The buffered text
        is consumed on the next ``_dispatch_producer`` call.
        """
        text = (text or "").strip()
        if not text:
            return
        pending = self.state.get("pending_user_feedback", "")
        self.state["pending_user_feedback"] = (pending + "\n\n" + text) if pending else text
        self._save()
        logger.info(
            "[PIPELINE] Queued CEO feedback (len={}) at stage {} phase {}",
            len(text), self.current_stage, self.phase,
        )

    def _consume_pending_feedback(self) -> str:
        text = self.state.get("pending_user_feedback", "")
        if text:
            self.state["pending_user_feedback"] = ""
            self._save()
        return text

    def _dispatch_producer(self, feedback: str = ""):
        """Dispatch the current stage's producer. Uses user assignment if set.

        Stage 6 is split into two sub-phases dispatched through this same
        method:
          - ``impl_producer``: code_implementer writes runnable Python and
            pushes it to the remote working dir.
          - ``exec_producer``: experiment_runner (or fallback) executes the
            code on the remote infra and captures run_id + metrics.
        Fresh entry into Stage 6 (current phase is the generic ``producer``)
        always starts with the implementation sub-phase.
        """
        stage = self._stage_def()

        # Stage 6 sub-phase resolution. The producer is dispatched either
        # because we just entered Stage 6 (phase=="producer") or because
        # impl_critic just PASSed and we're now kicking off execution
        # (phase=="exec_producer", already set by on_task_complete).
        stage6_subphase: str | None = None
        if stage["id"] == 6:
            if self.phase == "exec_producer":
                stage6_subphase = "exec_producer"
            else:
                stage6_subphase = "impl_producer"

        # Check if user assigned a specific employee to this stage
        assignments = self.state.get("stage_assignments", {})
        assigned = assignments.get(str(stage["id"]))
        if assigned:
            employee_id = assigned
        elif stage6_subphase is not None:
            employee_id = _find_employee_for_stage6_subphase(stage6_subphase)
        else:
            employee_id = _find_employee_for_stage(stage["id"], stage["skill"])
        if not employee_id:
            missing = (
                "code_implementer" if stage6_subphase == "impl_producer"
                else stage["skill"]
            )
            logger.error(
                "[PIPELINE] No employee with skill '{}' for stage {} (sub-phase={})",
                missing, stage["id"], stage6_subphase,
            )
            self.state["phase"] = "failed"
            self._save()
            return

        context = self._build_context()
        memory_guidance = self._retrieve_memory_guidance(stage, context, feedback)
        desc = (
            f"Stage {stage['id']}: {stage['name']}\n\n"
            f"{context}\n"
        )
        if memory_guidance:
            desc += f"\n--- Retrieved Research Memory ---\n{memory_guidance}\n"
        if feedback:
            desc += f"\nFeedback from previous review:\n{feedback}\n"
        user_feedback = self._consume_pending_feedback()
        if user_feedback:
            desc += f"\nDirect guidance from CEO (received during the previous attempt):\n{user_feedback}\n"
        # Stage 4 (Methodology Design) must run a multi-agent debate before
        # writing the methodology. The convener skill is the runbook.
        if stage["id"] == 4:
            desc += (
                "\n## REQUIRED FIRST STEP\n"
                'Before doing anything else, call load_skill("methodology-debate-convener") '
                "and follow the runbook exactly. It walks you through the full "
                "draft → debate → revise flow: assemble a diverse team, write a v1 "
                "methodology draft, convene a debate that critiques the draft, save "
                "the transcript, and revise v1 into a CCF-A-grade final methodology "
                "(8 sections, English only). Do not skip any phase.\n"
            )
        # Stage 5 (Experiment Design) mirrors the Stage 4 flow: draft → debate
        # → revise → coordination (assignments table). The experiment convener
        # skill is the runbook.
        elif stage["id"] == 5:
            desc += (
                "\n## REQUIRED FIRST STEP\n"
                'Before doing anything else, call load_skill("experiment-debate-convener") '
                "and follow the runbook exactly. It walks you through reading the Stage 4 "
                "methodology, drafting an initial experiment plan, debating it with the "
                "team, revising it into a CCF-A-grade experiment plan, and producing a "
                "coordination assignments table for Stage 6 execution. Do not write the "
                "experiment plan directly without convening the debate first.\n"
            )
        # Stage 6 (Auto Experiment) is split into two sub-phases. The
        # implementation sub-phase translates Stage 5's prose plan into
        # runnable code and pushes it to the remote working dir. The
        # execution sub-phase then runs the pushed code on the remote
        # infra. We send a different REQUIRED FIRST STEP block depending
        # on which sub-phase we're in.
        elif stage["id"] == 6 and stage6_subphase == "impl_producer":
            desc += (
                "\n## REQUIRED FIRST STEP — Stage 6a (Implementation)\n"
                'Before doing anything else, call load_skill("code-implementation-runbook") '
                "and follow it exactly. Your job is translation, not "
                "redesign: read the Stage 5 experiment plan and produce "
                "Python code that implements it bit-for-bit, then push the "
                "code to the remote working dir via experiment-infra's "
                "fast_push_code.sh. Hard rules: never substitute mock data "
                "for real benchmarks (e.g. GSM8K means the real dataset, "
                "not a hardcoded sample list); never introduce IVs or DVs "
                "not present in Stage 4/5; write all output in English. "
                "Document any spec ambiguities in your receipt instead of "
                "improvising.\n"
            )
        elif stage["id"] == 6 and stage6_subphase == "exec_producer":
            desc += (
                "\n## REQUIRED FIRST STEP — Stage 6b (Execution)\n"
                'Before doing anything else, call load_skill("experiment-execution-runbook") '
                "and follow it. The runbook tells you how to read "
                "stage5_assignments.md and route each row by its `skill` "
                "column. For rows tagged `experiment_runner`, you also have "
                'load_skill("experiment-infra") available — that gives you the '
                "fast_*.sh scripts to submit real runs to the remote infra, "
                "poll status, and capture log_tail + metrics. Stage 6a has "
                "already pushed code to the remote working dir, so your job "
                "is to submit and monitor — not to rewrite the code. Do not "
                "fabricate or simulate results — if a remote submit is "
                "required but credentials are missing, report the failure.\n"
                "\n## COMPLETION CRITERIA — NOT OPTIONAL\n"
                "The task is considered complete only when BOTH of these are "
                "true:\n"
                "  1. You have called write() with file_path ending in "
                "`stage6_experimentalist.md` and content ≥1500 bytes "
                "(use the runbook's template; fill TBD where you lack data).\n"
                "  2. You have called submit_result() with a `summary` "
                "string ≥100 characters that references "
                "`stage6_experimentalist.md` AND lists at least one run_id "
                "from your fast_submit calls.\n"
                "If you terminate the task without doing both, the engine "
                "auto-REJECTs you, redispatches you with this exact warning "
                "attached, and the GPU time you already burned is wasted. "
                "The most common LLM failure mode here is feeling 'done' "
                "after the run_id is captured — that is NOT done. Step 1 "
                "(write) and step 2 (submit_result) come AFTER you have a "
                "run_id, not before.\n"
            )
        # Stage 7 (Result Analysis) reads the Stage 4 methodology, the
        # Stage 5 experiment plan + assignments, and the Stage 6
        # experimentalist report, then produces a confirmatory analysis
        # that obeys the pre-registered tests and labels every claim as
        # confirmatory or exploratory. HARKing is auto-REJECTED.
        elif stage["id"] == 7:
            desc += (
                "\n## REQUIRED FIRST STEP\n"
                'Before doing anything else, call load_skill("result-analysis-runbook") '
                "and follow it. The runbook tells you how to reconstruct the "
                "pre-registration contract from Stage 4/5, map Stage 6 evidence "
                "onto each hypothesis, run only the pre-registered statistical "
                "tests with effect sizes + 95% CIs, run the manipulation and "
                "falsification checks, and cap the overall verdict at whatever "
                "coverage Stage 6 actually delivered. Do not invent new tests, "
                "do not substitute metrics, do not HARK.\n"
            )
        desc += (
            f"\nYour task: produce the deliverable for this stage. "
            f"Write your output to a file named stage{stage['id']}_{stage['skill']}.md "
            f"in the project workspace using the write() tool. "
            f"Then call submit_result() with a summary."
        )

        # Set the phase. Stage 6 uses sub-phase labels so on_task_complete
        # can route correctly when the task returns; other stages keep
        # the generic "producer".
        if stage6_subphase is not None:
            self.state["phase"] = stage6_subphase
        else:
            self.state["phase"] = "producer"
        self._save()
        self._dispatch_to_employee(employee_id, desc, f"Stage {stage['id']}: {stage['name']}")
        # Resolve employee name for frontend display
        emp_name = employee_id
        configs = load_employee_configs()
        if employee_id in configs:
            emp_name = configs[employee_id].name
        self._emit_stage_event("stage_start", stage["id"], employee_name=emp_name, employee_id=employee_id)

    def _dispatch_critic(self, producer_result: str):
        """Dispatch the adversarial critic to review the producer's output.

        For Stage 6 we route to one of two sub-critics depending on which
        sub-phase just finished:
          - impl_producer just finished → dispatch impl_critic
            (code-quality-critic runbook).
          - exec_producer just finished → dispatch exec_critic
            (the existing run_id / fabrication check).
        """
        stage = self._stage_def()

        # Decide Stage 6 sub-critic from the current phase. on_task_complete
        # calls _dispatch_critic immediately after storing the producer
        # result, so self.phase still reflects which sub-producer just ran.
        stage6_subphase: str | None = None
        if stage["id"] == 6:
            if self.phase == "impl_producer":
                stage6_subphase = "impl_critic"
            elif self.phase == "exec_producer":
                stage6_subphase = "exec_critic"

        critic_id = _find_employee_by_skill(CRITIC_SKILL)
        if not critic_id:
            logger.warning("[PIPELINE] No critic employee found, auto-passing stage {}", stage["id"])
            self._record_stage_memory(
                stage,
                producer_result=producer_result,
                critic_result="No adversarial critic was available; auto-pass.",
                passed=True,
                confidence=None,
                outcome="auto_pass",
            )
            self._on_critic_pass(producer_result)
            return

        desc = (
            f"Gate Review: Stage {stage['id']} ({stage['name']})\n\n"
            f"Review the following output and provide:\n"
            f"1. A confidence score (0.0 to 1.0)\n"
            f"2. A PASS or REJECT decision\n"
            f"3. Specific reasoning\n\n"
            f"If REJECT, explain exactly what needs to be improved.\n\n"
        )
        # Stage 3 (Idea Generation) is produced by the literature-conflict-graph
        # (aigraph) tool, so its deliverable is a `# Selected Hypotheses` report —
        # NOT the generic idea-generation document. Grade it on that basis.
        if stage["id"] == 3:
            desc += (
                "## STAGE 3 IS A LITERATURE-CONFLICT-GRAPH DELIVERABLE\n"
                "The Stage 3 deliverable is generated by the aigraph "
                "literature-conflict-graph tool. Its expected shape is a "
                "`# Stage 3: Idea Generation — <topic>` heading followed by a "
                "`# Selected Hypotheses` report with `### Anomaly a… —` and "
                "hypothesis items grounded in real claim citations. Hypothesis "
                "items appear as `### h… —` (critic / conflict-explanation ideas) "
                "or `### a…#cr… —` (creator / new-method-proposal ideas); BOTH "
                "are valid. This format is correct and intentional — it is NOT "
                "hand-written prose.\n"
                "PASS when: a topic heading is present, there is a "
                "`# Selected Hypotheses` section, and it contains at least one "
                "hypothesis (`### h…` or `### a…#cr…`) citing claim IDs. Do NOT require the "
                "generic idea-generation sections (evaluation architecture, "
                "method pseudocode, risk tables, outcome scenarios) — those "
                "belong to Stages 4–5, not here. Only REJECT if the report is "
                "empty, has zero hypotheses, or says `_No matches for topic_`.\n\n"
            )
        # Stage 4 (Methodology Design) is graded against a CCF-A quality
        # checklist. Load the runbook first so the critic applies the same
        # bar an ICML/NeurIPS reviewer would.
        elif stage["id"] == 4:
            desc += (
                "## REQUIRED FIRST STEP\n"
                'Before reading the producer output, call '
                'load_skill("methodology-quality-critic") and follow that '
                "runbook to grade the methodology against CCF-A criteria "
                "(formalism, algorithmic detail, statistical rigor, "
                "reproducibility, threats-to-validity depth, citation of the "
                "debate transcript). Reject confidently when any required "
                "section is shallow or missing.\n\n"
            )
        elif stage["id"] == 5:
            desc += (
                "## REQUIRED FIRST STEP\n"
                'Before reading the producer output, call '
                'load_skill("experiment-quality-critic") and follow that '
                "runbook to grade the experiment plan and coordination "
                "assignments against CCF-A criteria (operational procedure, "
                "sample-size/power math, pre-registration spec, failure-mode "
                "mitigations, reproducibility, debate citation, and a fully "
                "populated assignments table). Reject confidently when any "
                "required section is shallow or missing.\n\n"
            )
        # Stage 6a (Implementation) critic: grade the pushed code against
        # the Stage 5 prose plan. Three auto-REJECT triggers spelled out
        # explicitly so the critic doesn't have to discover them.
        elif stage["id"] == 6 and stage6_subphase == "impl_critic":
            desc += (
                "## REQUIRED FIRST STEP — Stage 6a Implementation Review\n"
                'Before reading the producer output, call '
                'load_skill("code-quality-critic") and follow that runbook. '
                "Grade the implementation against the Stage 5 plan: does "
                "the pushed code match the spec exactly, was it pushed via "
                "fast_push_code.sh, and is the receipt complete?\n"
                "Three auto-REJECT triggers (no second chances):\n"
                "  (a) Mock/hardcoded data used where the spec called for "
                "a real benchmark (e.g. GSM8K reduced to a hand-typed list "
                "of questions).\n"
                "  (b) New IVs or DVs introduced that weren't in Stage 4/5.\n"
                "  (c) Non-English code, comments, or receipt.\n"
                "Reject confidently when any of these are present.\n\n"
            )
        # Stage 6b (Execution) critic: the original Stage 6 critic — checks
        # that the report is grounded in real run_ids (not fabricated), that
        # every assignments-table row is accounted for, and that remote
        # runs report status + cost + log_tail.
        elif stage["id"] == 6:
            desc += (
                "## REQUIRED FIRST STEP — Stage 6b Execution Review\n"
                "Grade the Stage 6 execution report by asking:\n"
                "  - Is every row of stage5_assignments.md addressed?\n"
                "  - For rows tagged `experiment_runner`, is there a real "
                "run_id, a terminal status, an actual_cost, and a log_tail "
                "excerpt? Fabricated/simulated results when a runner was "
                "available are an auto-REJECT.\n"
                "  - For rows deferred to non-runner assignees, is the "
                "deferral explicit (not silent)?\n"
                "  - Does the aggregate summary tally total tasks, "
                "successes, failures, and total cost?\n"
                "Reject when the report claims success without a verifiable "
                "run_id.\n\n"
            )
        # Stage 7 critic enforces the pre-registration contract: every
        # confirmatory claim in Stage 7 must trace back to a Stage 4/5
        # pre-registered test and a real Stage 6 run_id. HARKing is an
        # explicit auto-REJECT trigger.
        elif stage["id"] == 7:
            desc += (
                "## REQUIRED FIRST STEP\n"
                'Before reading the producer output, call '
                'load_skill("result-quality-critic") and follow that '
                "runbook to grade Stage 7 against the immutable Stage 4/5 "
                "pre-registration contract and the actual Stage 6 evidence. "
                "Three auto-REJECT triggers: (a) any test in Stage 7 "
                "confirmatory section not present verbatim in Stage 4/5 "
                "(HARKing); (b) any confirmatory claim without a real "
                "Stage 6 run_id (fabrication); (c) non-English document.\n\n"
            )
        elif stage["id"] == 8:
            desc += (
                "## REQUIRED FIRST STEP — Stage 8 (Paper Generation) review\n"
                "Read both the producer's paper draft AND "
                "`stage4_methodology_designer.md` from the project workspace, "
                "then verify:\n"
                "  (a) **Math notation preservation (HARD GATE).** Count the "
                "number of `$$...$$` block equations and `$...$` inline math "
                "in Stage 4. The Stage 8 Methodology section MUST contain "
                "equations rendered in the SAME LaTeX syntax — flattening "
                "`$\\sum_i Y_i$` into `sum_i Y_i` plain-text is auto-REJECT. "
                "If Stage 4 has ≥3 LaTeX equation blocks and Stage 8's "
                "Methodology section has zero, REJECT.\n"
                "  (b) **Numerical traceability.** Every numeric claim "
                "(accuracies, p-values, CIs, contingency counts) in Stage 8 "
                "must match Stage 7 exactly.\n"
                "  (c) **No fabricated references.** Every cited paper must "
                "appear in Stage 4 or Stage 5 references, OR be a clearly "
                "well-known foundational work the producer flags as new.\n"
                "  (d) **No HARK.** Every confirmatory claim must trace to "
                "a Stage 4/5 pre-registered hypothesis.\n"
                "  (e) **Honest limitations.** Stage 7's documented coverage "
                "gaps (e.g. manipulation check not captured) must be "
                "transcribed into the Limitations section.\n\n"
            )
        desc += f"--- Producer Output ---\n{producer_result}\n"

        # For Stage 6, use the sub-critic phase label; otherwise use the
        # generic "critic" phase that other stages rely on.
        if stage6_subphase is not None:
            self.state["phase"] = stage6_subphase
        else:
            self.state["phase"] = "critic"
        self._save()
        self._dispatch_to_employee(critic_id, desc, f"Gate Review: Stage {stage['id']}")

    def on_task_complete(self, employee_id: str, node_id: str, result: str):
        """Called by vessel when a pipeline-managed task completes.

        Stage 6 has four extra phases — impl_producer, impl_critic,
        exec_producer, exec_critic — handled separately so the engine can
        run the implementation/review/execution/review chain under a
        single stage_id.
        """
        stage = self._stage_def()

        # ------------------------------------------------------------------
        # Stage 6 sub-phase handling
        # ------------------------------------------------------------------
        if stage["id"] == 6 and self.phase in (
            "impl_producer", "impl_critic", "exec_producer", "exec_critic",
        ):
            if self.phase == "impl_producer":
                # Code implementer finished → store the implementation
                # receipt and hand it to the code-quality critic.
                self.state.setdefault("stage_results", {})["6_impl"] = result
                self._save()
                logger.info(
                    "[PIPELINE] Stage 6a (impl) producer complete — dispatching impl_critic",
                )
                self._emit_stage_event("stage_reviewing", 6)
                self._dispatch_critic(result)
                return

            if self.phase == "impl_critic":
                # Code-quality critic finished → parse PASS/REJECT.
                is_pass, confidence, effective = self._parse_critic_with_fallback(result)
                # Persist the EFFECTIVE text (file-recovered if the submit_result
                # was a stub) so downstream stages and the UI see the real verdict.
                self.state["critic_result"] = effective
                self._save()
                self._emit_critic_result(6, effective, is_pass, confidence)

                if is_pass:
                    logger.info(
                        "[PIPELINE] Stage 6a (impl) PASSED — transitioning to exec_producer "
                        "(confidence={})", confidence,
                    )
                    # Hand off to the execution sub-phase. Reset exec_retries
                    # so a runner-side retry budget is fresh.
                    self.state["phase"] = "exec_producer"
                    self.state["exec_retries"] = 0
                    self._save()
                    self._dispatch_producer()
                else:
                    impl_retries = self.state.get("impl_retries", 0)
                    if impl_retries < STAGE6_IMPL_MAX_RETRIES:
                        self.state["impl_retries"] = impl_retries + 1
                        self._save()
                        logger.info(
                            "[PIPELINE] Stage 6a (impl) REJECTED (retry {}/{}) — re-dispatching code_implementer",
                            impl_retries + 1, STAGE6_IMPL_MAX_RETRIES,
                        )
                        self._emit_stage_event("stage_failed", 6, confidence=confidence)
                        # Re-enter impl_producer (set explicitly so the
                        # producer-dispatch path routes back to the
                        # code_implementer, not the runner).
                        self.state["phase"] = "impl_producer"
                        self._save()
                        self._dispatch_producer(feedback=result)
                    else:
                        logger.warning(
                            "[PIPELINE] Stage 6a (impl) exhausted retries — holding for CEO",
                        )
                        self.state["phase"] = "gate"
                        self._save()
                        self._emit_gate_event(6, confidence, exhausted=True)
                return

            if self.phase == "exec_producer":
                # Experiment runner finished → store under the canonical
                # Stage 6 key so downstream stages see the execution
                # report, then dispatch the exec_critic.
                self.state.setdefault("stage_results", {})[str(6)] = result
                self._save()
                # Short-circuit on stub producer output (agent terminated
                # without calling submit_result; the executor captured a
                # placeholder like "Executed: bash"). Going through the
                # critic burns ~1 min and the critic almost always
                # mis-parses the stub. Force REJECT + retry directly.
                if _is_stub_producer_result(result):
                    exec_retries = self.state.get("exec_retries", 0)
                    if exec_retries < MAX_RETRIES:
                        self.state["exec_retries"] = exec_retries + 1
                        self._save()
                        logger.warning(
                            "[PIPELINE] Stage 6b producer returned stub result "
                            "({} chars: {!r}) — skipping critic, redispatching "
                            "runner (retry {}/{})",
                            len(result), result[:50],
                            exec_retries + 1, MAX_RETRIES,
                        )
                        self._emit_stage_event("stage_failed", 6)
                        self._dispatch_producer(
                            feedback=(
                                f"Your previous attempt terminated without writing "
                                f"`stage6_experimentalist.md` or calling `submit_result()` "
                                f"with a real summary. The engine captured the "
                                f"placeholder string {result!r} as your output, which "
                                f"the engine auto-REJECTs. Re-read the "
                                f"experiment-execution-runbook (specifically Step 3 — "
                                f"write file is MANDATORY — and Step 4 — submit_result "
                                f"summary must be ≥100 chars). Do NOT terminate the task "
                                f"until both steps are done, even if the experiment is "
                                f"still running on remote infra. A partial report with "
                                f"`status: still_running` is the correct output for a "
                                f"long experiment."
                            )
                        )
                        return
                    # Otherwise fall through to gate-exhausted below.
                    logger.warning(
                        "[PIPELINE] Stage 6b producer stub but retries exhausted "
                        "— holding for CEO",
                    )
                    self.state["phase"] = "gate"
                    self._save()
                    self._emit_gate_event(6, confidence=None, exhausted=True)
                    return

                # Smoke-failure short-circuit: the runner explicitly reported
                # that the smoke run failed, which almost always means the
                # Stage 6a implementation has a bug. The runner did the right
                # thing by NOT submitting the full run, but retrying the runner
                # won't help — the bug is in the code, not the runner. Route
                # back to impl_producer with the smoke log as feedback so the
                # code implementer can target the actual error.
                smoke_marker = _detect_smoke_failure(result)
                if smoke_marker:
                    impl_retries = self.state.get("impl_retries", 0)
                    if impl_retries < STAGE6_IMPL_MAX_RETRIES:
                        self.state["impl_retries"] = impl_retries + 1
                        # Reset exec_retries — the next exec_producer attempt
                        # is dispatching code, not running it, so the runner
                        # retry budget should be fresh once impl resubmits.
                        self.state["exec_retries"] = 0
                        self.state["phase"] = "impl_producer"
                        self._save()
                        logger.warning(
                            "[PIPELINE] Stage 6b runner reported smoke failure "
                            "({}) — routing back to impl_producer for code fix "
                            "(impl retry {}/{})",
                            smoke_marker, impl_retries + 1, STAGE6_IMPL_MAX_RETRIES,
                        )
                        self._emit_stage_event("stage_failed", 6)
                        self._dispatch_producer(
                            feedback=(
                                f"The Stage 6b runner submitted the `--smoke` "
                                f"test you exposed in `experiment.py`, and it "
                                f"FAILED before completing. The runner correctly "
                                f"halted instead of submitting the full run. The "
                                f"bug is in your implementation — fix it.\n\n"
                                f"Full runner report (read it for the run_id, "
                                f"log_tail, and exact error message):\n"
                                f"---\n{result[:4000]}\n---\n\n"
                                f"Action required: identify the root cause "
                                f"named in the log_tail (e.g. `UnboundLocalError`, "
                                f"`KeyError`, missing-file, etc.), patch the "
                                f"offending code locally, push the updated file "
                                f"to the per-project remote subdir, and update "
                                f"the implementation receipt accordingly. Then "
                                f"the runner will re-run smoke."
                            )
                        )
                        return
                    logger.warning(
                        "[PIPELINE] Stage 6b smoke kept failing across {} impl "
                        "retries — holding for CEO", MAX_RETRIES,
                    )
                    self.state["phase"] = "gate"
                    self._save()
                    self._emit_gate_event(6, confidence=None, exhausted=True)
                    return

                logger.info(
                    "[PIPELINE] Stage 6b (exec) producer complete — dispatching exec_critic",
                )
                self._emit_stage_event("stage_reviewing", 6)
                self._dispatch_critic(result)
                return

            if self.phase == "exec_critic":
                # Run-id / fabrication critic finished.
                is_pass, confidence, effective = self._parse_critic_with_fallback(result)
                self.state["critic_result"] = effective
                self._save()
                self._emit_critic_result(6, effective, is_pass, confidence)

                if is_pass:
                    logger.info(
                        "[PIPELINE] Stage 6b (exec) PASSED — opening CEO gate (confidence={})",
                        confidence,
                    )
                    self._on_critic_pass(
                        self.state["stage_results"].get(str(6), ""), confidence,
                    )
                else:
                    exec_retries = self.state.get("exec_retries", 0)
                    if exec_retries < MAX_RETRIES:
                        self.state["exec_retries"] = exec_retries + 1
                        self.state["phase"] = "exec_producer"
                        self._save()
                        logger.info(
                            "[PIPELINE] Stage 6b (exec) REJECTED (retry {}/{}) — re-dispatching runner",
                            exec_retries + 1, MAX_RETRIES,
                        )
                        self._emit_stage_event("stage_failed", 6, confidence=confidence)
                        self._dispatch_producer(feedback=result)
                    else:
                        logger.warning(
                            "[PIPELINE] Stage 6b (exec) exhausted retries — holding for CEO",
                        )
                        self.state["phase"] = "gate"
                        self._save()
                        self._emit_gate_event(6, confidence, exhausted=True)
                return

        # ------------------------------------------------------------------
        # Stages 1-5, 7-9 (and Stage 6 fresh-entry where phase is still
        # the generic "producer") use the original flow.
        # ------------------------------------------------------------------
        if self.phase == "producer":
            # Producer finished → store result, dispatch critic
            # Stage 3 (literature-conflict-graph) deliverable is the FILE the
            # aigraph tool writes (`# Selected Hypotheses` report). The agent's
            # chat result is often just a summary, which the UI can't render as
            # a conflict graph — so prefer the file content as the stage result
            # (the critic reads the file too, keeping them consistent).
            if stage["id"] == 3:
                from pathlib import Path
                deliverable = Path(self.project_dir) / f"stage3_{stage['skill']}.md"
                try:
                    if deliverable.exists():
                        file_text = deliverable.read_text(encoding="utf-8").strip()
                        if "# Selected Hypotheses" in file_text:
                            result = file_text
                except Exception as e:
                    logger.debug("[PIPELINE] Stage 3 file-content fallback failed: {}", e)
            self.state["stage_results"][str(stage["id"])] = result
            self._save()
            logger.info("[PIPELINE] Stage {} producer complete, dispatching critic", stage["id"])
            self._emit_stage_event("stage_reviewing", stage["id"])
            self._dispatch_critic(result)

        elif self.phase == "critic":
            # Critic finished → parse decision
            is_pass, confidence, effective = self._parse_critic_with_fallback(result)
            # Persist the EFFECTIVE text (file-recovered if stub) so the UI and
            # downstream stages see the real verdict, not the executor placeholder.
            self.state["critic_result"] = effective
            self._save()

            # Emit critic result to frontend so it shows in the stage card
            self._emit_critic_result(stage["id"], effective, is_pass, confidence)

            if is_pass:
                self._record_stage_memory(
                    stage,
                    producer_result=self.state["stage_results"].get(str(stage["id"]), ""),
                    critic_result=result,
                    passed=True,
                    confidence=confidence,
                    outcome="critic_pass",
                )
                self._save()
                logger.info("[PIPELINE] Stage {} PASSED (confidence={})", stage["id"], confidence)
                self._on_critic_pass(self.state["stage_results"].get(str(stage["id"]), ""), confidence)
            else:
                retries = self.state.get("retries", 0)
                if retries < MAX_RETRIES:
                    self._record_stage_memory(
                        stage,
                        producer_result=self.state["stage_results"].get(str(stage["id"]), ""),
                        critic_result=result,
                        passed=False,
                        confidence=confidence,
                        outcome="critic_reject_retry",
                    )
                    self.state["retries"] = retries + 1
                    self._save()
                    logger.info("[PIPELINE] Stage {} REJECTED (retry {}/{})", stage["id"], retries + 1, MAX_RETRIES)
                    self._emit_stage_event("stage_failed", stage["id"], confidence=confidence)
                    self._dispatch_producer(feedback=result)
                else:
                    self._record_stage_memory(
                        stage,
                        producer_result=self.state["stage_results"].get(str(stage["id"]), ""),
                        critic_result=result,
                        passed=False,
                        confidence=confidence,
                        outcome="critic_reject_exhausted",
                    )
                    logger.warning("[PIPELINE] Stage {} exhausted retries, holding for CEO", stage["id"])
                    self.state["phase"] = "gate"
                    self._save()
                    self._emit_gate_event(stage["id"], confidence, exhausted=True)

    def on_task_failed(self, employee_id: str, node_id: str, result: str):
        """Called by vessel when a pipeline-managed task fails (the agent
        threw, timed out, or otherwise produced no usable output).

        Branches on the current phase:

        * ``producer`` failure → retry the producer with the failure
          context as feedback (up to ``MAX_RETRIES``), then open the CEO
          gate. Symmetric with a critic REJECT.

        * ``critic`` failure → auto-pass the stage using the already-stored
          producer output. Mirrors the "no critic employee found" branch
          in ``_dispatch_critic``. Re-running the producer would discard
          its existing output and double-bill tokens for a problem that
          isn't the producer's.

        Without this hook a failed pipeline node would fall through to
        vessel.py's legacy completion check, which would mistake the
        first-completed stage anchor for an EA orchestrator and declare
        the project complete.
        """
        stage = self._stage_def()
        current_phase = self.phase

        # Treat Stage 6 sub-critic phases like the generic "critic" path:
        # the producer output is already stored, so auto-pass on it rather
        # than burning a retry on a critic-side glitch.
        if current_phase in ("critic", "impl_critic", "exec_critic"):
            # For impl_critic the stored producer output lives under
            # "6_impl"; for exec_critic and the generic critic it lives
            # under the stage id.
            key = "6_impl" if current_phase == "impl_critic" else str(stage["id"])
            stored = self.state.get("stage_results", {}).get(key, "")
            logger.warning(
                "[PIPELINE] Stage {} critic FAILED (phase={}) — auto-passing on stored producer output (len={})",
                stage["id"], current_phase, len(stored),
            )
            if current_phase == "impl_critic":
                # Mirror the impl_critic PASS branch: hand off to execution
                # rather than opening the CEO gate yet.
                self.state["phase"] = "exec_producer"
                self.state["exec_retries"] = 0
                self._save()
                self._dispatch_producer()
            else:
                self._on_critic_pass(stored, confidence=None)
            return

        if current_phase not in ("producer", "impl_producer", "exec_producer"):
            # Should not happen — gate/done/failed phases mean no task is in flight.
            logger.warning(
                "[PIPELINE] on_task_failed called in unexpected phase {} (stage {}); ignoring",
                current_phase, stage["id"],
            )
            return

        truncated = (result or "(no output)").strip()[:600]
        failure_feedback = (
            f"Producer for Stage {stage['id']} ({stage['name']}) failed without producing a deliverable. "
            f"Failure context:\n{truncated}"
        )
        # Choose the retry counter keyed off which sub-phase failed; the
        # generic "producer" path keeps the legacy "retries" counter so
        # Stages 1-5 and 7-9 are unaffected.
        if current_phase == "impl_producer":
            counter_key = "impl_retries"
            restore_phase = "impl_producer"
        elif current_phase == "exec_producer":
            counter_key = "exec_retries"
            restore_phase = "exec_producer"
        else:
            counter_key = "retries"
            restore_phase = "producer"
        retries = self.state.get(counter_key, 0)
        if retries < MAX_RETRIES:
            self.state[counter_key] = retries + 1
            self.state["phase"] = restore_phase
            self._save()
            logger.warning(
                "[PIPELINE] Stage {} producer FAILED (phase={}, retry {}/{}) — re-dispatching",
                stage["id"], current_phase, retries + 1, MAX_RETRIES,
            )
            self._emit_stage_event("stage_failed", stage["id"])
            self._dispatch_producer(feedback=failure_feedback)
        else:
            logger.error(
                "[PIPELINE] Stage {} exhausted retries after producer failure (phase={}) — holding for CEO",
                stage["id"], current_phase,
            )
            self.state["phase"] = "gate"
            self._save()
            self._emit_gate_event(stage["id"], confidence=None, exhausted=True)

    def _on_critic_pass(self, result: str, confidence: float = None):
        """Critic passed → hold for CEO gate."""
        stage = self._stage_def()
        self.state["phase"] = "gate"
        self._save()
        # Commit the workspace as the canonical checkpoint for this stage
        # before opening the gate. This is the quiescent moment: producer
        # and critic are both finished, nothing else is writing files.
        # The tag ``<iteration>/stage-<N>`` lets the user later revert
        # here to redo this stage with new instructions.
        from onemancompany.core import project_repo
        try:
            project_repo.commit_stage(
                self.project_dir,
                iteration=self._iteration_id(),
                stage=stage["id"],
                stage_name=stage["name"],
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "[PIPELINE] commit_stage failed at stage {}: {}", stage["id"], exc,
            )
        self._emit_stage_event("stage_complete", stage["id"], confidence=confidence)
        self._emit_gate_event(stage["id"], confidence)

    async def _cancel_active_task_and_wait(self, *, timeout: float = 5.0) -> None:
        """Cancel the engine's active producer/critic task and wait for it
        to actually terminate.

        ``asyncio.Task.cancel()`` is non-blocking — it schedules cancellation;
        the task only stops on its next ``await``. If we returned right after
        calling cancel and proceeded to ``git reset --hard``, the cancelled
        producer could still land a ``write()`` between our reset and the
        checkout. We grab the task handle *before* calling
        ``abort_employee`` (which pops it from ``_running_tasks``), then
        ``await`` it with a timeout so the cancellation has actually
        propagated through ``_run_task``'s finally block.
        """
        if self.phase in ("gate", "done", "failed"):
            return
        emp_id = self.state.get("active_employee_id")
        if not emp_id:
            return

        from onemancompany.core.agent_loop import employee_manager

        # Capture the task handle before abort_employee pops it.
        running = employee_manager._running_tasks.get(emp_id)
        try:
            cancelled = employee_manager.abort_employee(emp_id)
            logger.info(
                "[PIPELINE] Cancelled {} active task(s) for employee {} before revert",
                cancelled, emp_id,
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "[PIPELINE] abort_employee({}) failed during revert: {}",
                emp_id, exc,
            )

        # Wait for the producer's await chain to unwind. We swallow
        # CancelledError (expected) and the task's own task-side
        # exceptions (they're already logged by _run_task's finally
        # block) — what matters here is that the task has finished, so
        # no further file writes can land.
        if running is not None and not running.done():
            import asyncio
            try:
                await asyncio.wait_for(asyncio.shield(running), timeout=timeout)
            except asyncio.CancelledError:
                # Expected: that's what abort_employee() asked for. Task has
                # finished unwinding, which is the post-condition we needed.
                logger.debug("[PIPELINE] Cancelled task for {} terminated cleanly", emp_id)
            except asyncio.TimeoutError:
                logger.warning(
                    "[PIPELINE] Producer for {} did not stop within {}s; "
                    "proceeding with revert anyway", emp_id, timeout,
                )
            except Exception as exc:
                logger.debug(
                    "[PIPELINE] Cancelled task raised {} during teardown (ignored)", exc,
                )

        self.state["active_node_id"] = None
        self.state["active_employee_id"] = None

    # ------------------------------------------------------------------
    # Public API — revert to a previous stage with new instructions
    # ------------------------------------------------------------------

    async def revert_to_stage(
        self, *, stage: int, instructions: str, branch_name: str | None = None,
    ) -> str:
        """Create a feature branch rooted at stage ``stage - 1``'s tag,
        switch the workspace to it, queue the user's instructions for
        the stage's producer, and re-dispatch.

        Returns the (possibly auto-generated) branch name so callers can
        surface it to the user.

        Raises ``ValueError`` for out-of-range stage numbers.

        Behaviour when a later stage is mid-flight (phase ∈ {producer,
        critic}): the active task is cancelled and any uncommitted
        workspace changes from that cancelled task are discarded before
        the checkout. Callers don't need to wait for a gate — the engine
        handles the cleanup so reverts work at any point in the pipeline.

        The semantics deliberately keep the *current branch's* tags and
        commits intact — reverting forks; it does not destroy history.
        """
        end = self.state.get("end_stage", 9)
        if not (1 <= stage <= end):
            raise ValueError(
                f"revert_to_stage: stage must be in [1, {end}]; got {stage}"
            )

        instructions = (instructions or "").strip()

        # Validate dispatchability BEFORE touching git or cancelling
        # tasks. The whole revert operation should be either fully
        # successful or fully a no-op; otherwise we leave the user on a
        # new branch with corrupt state and no in-flight task.
        # ``stage_assignments`` honours user overrides; otherwise the
        # engine resolves by skill from ``employee_configs``.
        stage_def = STAGES[stage - 1]
        assignments = self.state.get("stage_assignments", {})
        assigned = assignments.get(str(stage_def["id"]))
        employee_id = assigned if assigned else _find_employee_by_skill(stage_def["skill"])
        if not employee_id:
            raise RevertNotAllowedError(
                f"Cannot revert to stage {stage}: no employee with skill "
                f"'{stage_def['skill']}' is available to run the producer."
            )

        # Cancel any in-flight producer/critic task before we touch git
        # and wait for it to actually stop (cancel() alone is non-blocking).
        # The cancelled task may have written partial output to the
        # workspace; ``discard_uncommitted_changes`` below scrubs that so
        # ``checkout_branch_from_stage``'s DirtyWorkspaceError guard
        # passes.
        was_mid_flight = self.phase in (
            "producer", "critic",
            "impl_producer", "impl_critic", "exec_producer", "exec_critic",
        )
        if was_mid_flight:
            await self._cancel_active_task_and_wait()

        from onemancompany.core import project_repo
        # Only scrub the workspace when we just cancelled a task. At
        # gate/done the workspace should already be clean (the previous
        # stage's commit_stage left it that way), and an unconditional
        # ``git reset --hard`` here would silently destroy any manual
        # edits the user made between gates. Let
        # ``checkout_branch_from_stage`` raise ``DirtyWorkspaceError``
        # loudly in that case.
        if was_mid_flight:
            project_repo.discard_uncommitted_changes(self.project_dir)
        new_branch = project_repo.checkout_branch_from_stage(
            self.project_dir,
            iteration=self._iteration_id(),
            stage=stage,
            branch_name=branch_name,
        )

        # The checkout flipped pipeline_state.yaml back to its previous
        # snapshot. Reload from disk; refuse to proceed if the snapshot
        # somehow lacks a state file (would silently retain the abandoned
        # branch's state otherwise — corrupting the new branch on the
        # next ``_save``).
        loaded = _load_state(self.project_dir)
        if not loaded:
            raise RevertNotAllowedError(
                f"Reverted to branch '{new_branch}' but the checkout did "
                f"not restore a pipeline_state.yaml. Workspace may be "
                f"corrupt — investigate before retrying."
            )
        self.state = loaded
        self.state["current_stage"] = stage
        self.state["phase"] = "producer"
        self.state["retries"] = 0
        self.state["critic_result"] = None
        self.state["active_node_id"] = None
        self.state["active_employee_id"] = None
        # Drop any stage results at or beyond the revert point — they
        # belong to the abandoned branch and would mislead the producer's
        # context-building.
        sr = self.state.get("stage_results", {})
        # Keys can be plain stage numbers ("4", "5") or Stage 6 sub-phase
        # keys ("6_impl", "6_exec"). Use the numeric prefix for the cutoff.
        self.state["stage_results"] = {
            sid: result for sid, result in sr.items()
            if int(str(sid).split("_", 1)[0]) < stage
        }
        # Queue the user's instructions; _dispatch_producer consumes them
        # via _consume_pending_feedback and prepends them to the prompt.
        if instructions:
            self.state["pending_user_feedback"] = instructions
        self._save()

        logger.info(
            "[PIPELINE] Reverted to stage {} on branch '{}' with {} chars of instructions",
            stage, new_branch, len(instructions),
        )
        self._dispatch_producer()
        return new_branch

    # Keywords that trigger a *full re-dispatch* of the current stage from
    # scratch (retries=0). Kept narrow on purpose: every CEO chat at the
    # gate flows through this matcher (since task_followup now routes
    # gate-phase feedback here), so any false positive silently undoes the
    # stage and confuses the user. Single-character triggers like "再" or
    # ambiguous edits like "修改" are excluded — they appear in legitimate
    # advance-with-comment chats ("再补充一点", "可以修改一下措辞") that
    # should NOT trigger a redo.
    _REVISION_KEYWORDS = (
        "REVISION", "REVISE", "RE-RUN", "REDO",
        "重新",  # "重新跑", "重新写", "重新做"
        "重做", "重写", "重跑",
        "再来一遍", "再做一遍", "再写一遍", "再跑一遍",
    )

    def on_ceo_approve(self, feedback: str = ""):
        """CEO approved the current stage. Advance or re-run."""
        stage = self._stage_def()

        if feedback and any(kw in feedback.upper() for kw in self._REVISION_KEYWORDS):
            # CEO wants revision
            logger.info("[PIPELINE] CEO requested revision for stage {}", stage["id"])
            self._apply_ceo_memory_feedback(stage, feedback, approved=False)
            self.state["retries"] = 0
            self._dispatch_producer(feedback=feedback)
            return

        self._apply_ceo_memory_feedback(stage, feedback, approved=True)

        # Advance to next stage
        end = self.state.get("end_stage", 9)
        if self.current_stage < end:
            next_stage = self.current_stage + 1
            self.state["current_stage"] = next_stage
            self.state["retries"] = 0
            self.state["critic_result"] = None
            self._save()
            logger.info("[PIPELINE] Advancing to stage {}", next_stage)
            self._dispatch_producer()
        else:
            self.state["phase"] = "done"
            self._save()
            logger.info("[PIPELINE] Pipeline complete!")
            self._emit_pipeline_complete()

    # ------------------------------------------------------------------
    # Critic result parsing
    # ------------------------------------------------------------------

    def _parse_critic_with_fallback(self, result: str) -> tuple[bool, float | None, str]:
        """Parse the critic's PASS/REJECT verdict AND its confidence,
        falling back to the on-disk ``stage{N}_gate_review*.md`` file
        when the critic's submit_result is a stub placeholder (e.g.
        ``"Executed: write"``).

        Returns ``(is_pass, confidence, effective_text)`` — callers
        should persist the effective text as the canonical critic
        result so downstream stages see the real verdict, not the
        executor stub.
        """
        if _is_stub_producer_result(result):
            recovered = _recover_verdict_from_gate_review(
                self.project_dir, self._stage_def()["id"],
            )
            if recovered:
                logger.info(
                    "[PIPELINE] critic submit_result was a stub ({} chars: {!r}); "
                    "recovered verdict from gate_review file ({} chars)",
                    len(result), result[:40], len(recovered),
                )
                return (
                    self._parse_critic_pass(recovered),
                    self._parse_confidence(recovered),
                    recovered,
                )
            logger.warning(
                "[PIPELINE] critic submit_result is a stub and no "
                "stage{}_gate_review*.md file found on disk — "
                "falling back to safe-REJECT", self._stage_def()["id"],
            )
        return (
            self._parse_critic_pass(result),
            self._parse_confidence(result),
            result,
        )

    @staticmethod
    def _parse_critic_pass(result: str) -> bool:
        """Determine whether the critic's decision is PASS.

        The critic's text typically includes references to "auto-REJECT"
        rules in its rubric explanation (e.g. "Auto-REJECT Trigger
        Check"), so a naive `"REJECT" in text` substring scan fires
        false positives even when the actual decision is PASS. This
        used to silently waste 3 retries × ~50s each on every Stage 4
        and Stage 6 run.

        Strategy: look for an explicit ``Decision: PASS|REJECT|FAIL``
        line first (with optional markdown bold/italic markers and
        case-insensitive matching). Only if no explicit decision line
        is found do we fall back to the legacy substring heuristic.
        """
        import re
        # Match: optional **/*/_ markup around the verdict-label
        # (Decision / Verdict / Result), then either a colon (``:``/``：``)
        # OR a markdown table cell separator (``|``), then optional markup,
        # then PASS / REJECT / FAIL. The pipe variant catches critics that
        # render the verdict in a table like
        #   | **Decision** | **PASS** |
        # which is a real-world pattern observed in Stage 4 reviews.
        # First match wins.
        decision_match = re.search(
            r'(?:\*\*|\*|_|`)*\s*(?:Decision|Verdict|Result)\s*(?:\*\*|\*|_|`)*\s*[:：|]\s*'
            r'(?:\*\*|\*|_|`)*\s*(PASS|REJECT|FAIL)\s*(?:\*\*|\*|_|`)*',
            result,
            re.IGNORECASE,
        )
        if decision_match:
            return decision_match.group(1).upper() == "PASS"

        # Fallback for critics that omit an explicit verdict line.
        # Heuristic: count occurrences of unambiguous verdict-context
        # phrases ("PASS overall", "overall PASS", "result: PASS" etc.).
        # If still ambiguous and BOTH keywords appear, prefer REJECT to
        # be safe — a real REJECT is too damaging to silently coerce
        # into PASS, while a false REJECT just costs one retry.
        upper = result.upper()
        has_reject = "REJECT" in upper or "FAIL" in upper
        has_pass = "PASS" in upper
        if has_reject and has_pass:
            # Ambiguous — be safe and reject
            return False
        if has_pass:
            return True
        if has_reject:
            return False
        # Default to REJECT when neither keyword is present. The most common
        # cause of a verdict-keyword-free result is a critic agent whose
        # submit_result was never called and the engine captured a stub
        # like ``"Executed: write"`` — coercing those to PASS lets a
        # REJECTing critic's verdict (typically saved to ``stage*_gate_review*.md``)
        # silently leak past the gate. False REJECTs cost one retry; false
        # PASSes corrupt the pipeline state forever.
        return False

    @staticmethod
    def _parse_confidence(result: str) -> float | None:
        import re
        # Match patterns like "confidence: 0.72" or "Confidence Score: 0.8".
        m = re.search(r'confidence(?:\s+score)?[:\s]*([01]\.?\d*)', result, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except ValueError as exc:
                logger.debug("Unable to parse confidence value '{}': {}", m.group(1), exc)
        return None

    # ------------------------------------------------------------------
    # Event emission (WebSocket → frontend)
    # ------------------------------------------------------------------

    async def _emit_async(self, payload: dict):
        await event_bus.publish(CompanyEvent(
            type=EventType.STATE_SNAPSHOT,
            payload=payload,
            agent=SYSTEM_AGENT,
        ))

    def _emit_critic_result(self, stage_id: int, critic_text: str, is_pass: bool, confidence: float = None):
        """Emit critic review result so frontend can display it in the stage card."""
        import asyncio
        payload = {
            "type": "critic_result",
            "stage": stage_id,
            "stage_name": self._stage_def(stage_id).get("name", ""),
            "project_id": self.project_id,
            "pipeline_managed": True,
            "decision": "PASS" if is_pass else "REJECT",
            "confidence": confidence,
            "text": critic_text,
        }
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._emit_async(payload))
        except RuntimeError as exc:
            logger.debug("Skipping critic_result event; no running event loop: {}", exc)

    def _emit_stage_event(self, event_type: str, stage_id: int, confidence: float = None, employee_name: str = "", employee_id: str = ""):
        """Emit stage lifecycle events for the frontend."""
        import asyncio
        payload = {
            "type": event_type,
            "stage": stage_id,
            "stage_name": self._stage_def(stage_id).get("name", ""),
            "employee_name": employee_name,
            "employee_id": employee_id,
            "project_id": self.project_id,
            "pipeline_managed": True,
        }
        if confidence is not None:
            payload["confidence"] = confidence
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._emit_async(payload))
        except RuntimeError as exc:
            logger.debug("Skipping stage event; no running event loop: {}", exc)

    def _emit_gate_event(self, stage_id: int, confidence: float = None, exhausted: bool = False):
        """Emit breakpoint/gate event for frontend to show approval dialog."""
        import asyncio
        payload = {
            "type": "breakpoint_hit",
            "stage": stage_id,
            "stage_name": self._stage_def(stage_id).get("name", ""),
            "project_id": self.project_id,
            "confidence": confidence,
            "retries_exhausted": exhausted,
            "message": f"Stage {stage_id} complete. Waiting for your approval.",
            "pipeline_managed": True,
        }
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._emit_async(payload))
            # Headless/unattended mode: advance the gate automatically so the
            # pipeline runs end-to-end with no human confirmation. Covers BOTH
            # gate openings (clean PASS and retries-exhausted) since both land
            # here. A human would otherwise click "approve" in the UI.
            if self.state.get("auto_approve"):
                loop.create_task(self._auto_approve_gate(stage_id, exhausted))
        except RuntimeError as exc:
            logger.debug("Skipping gate event; no running event loop: {}", exc)

    async def _auto_approve_gate(self, stage_id: int, exhausted: bool):
        """Unattended-mode gate advance: behaves like a CEO clicking approve."""
        import asyncio
        await asyncio.sleep(0)  # let the gate event flush first
        if self.phase != "gate":
            return
        logger.info(
            "[PIPELINE] AUTO-APPROVE (unattended): advancing gate at stage {} "
            "(exhausted={})", stage_id, exhausted,
        )
        self.on_ceo_approve("")

    def _emit_pipeline_complete(self):
        import asyncio
        # Close the CEO root in the task tree so the UI's
        # "project complete" affordance fires HERE — at the end of the
        # pipeline — instead of at the legacy EA-anchor completion point
        # (which mis-fired after Stage 1).
        self._mark_ceo_root_finished()
        payload = {
            "type": "pipeline_complete",
            "project_id": self.project_id,
            "stages_completed": len(self.state.get("stage_results", {})),
            "pipeline_managed": True,
        }
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._emit_async(payload))
        except RuntimeError as exc:
            logger.debug("Skipping pipeline complete event; no running event loop: {}", exc)

    def _mark_ceo_root_finished(self) -> None:
        """On pipeline completion, walk the CEO root through legal status
        transitions to FINISHED so downstream consumers (project archive,
        frontend completion banner) see the project as closed.

        Status transitions are validated against ``VALID_TRANSITIONS`` per
        step. If any step is illegal (e.g. the root is in BLOCKED/FAILED/
        CANCELLED), the method logs a warning and bails — the caller
        should not assume the root reached FINISHED.
        """
        from onemancompany.core.task_tree import get_tree
        from onemancompany.core.task_lifecycle import (
            NodeType, TaskPhase, can_transition,
        )
        from onemancompany.core.config import TASK_TREE_FILENAME

        tree_path = Path(self.project_dir) / TASK_TREE_FILENAME
        if not tree_path.exists():
            return
        try:
            tree = get_tree(tree_path, project_id=self.project_id)
            root = tree.get_node(tree.root_id) if tree.root_id else None
            if not root or root.node_type != NodeType.CEO_PROMPT:
                return
            if root.status == TaskPhase.FINISHED.value:
                return  # already terminal — idempotent

            # FAILED/CANCELLED roots are explicitly out of scope: the project
            # was marked failed/cancelled elsewhere (e.g. vessel root-failed
            # path), so finalizing it as a completed pipeline would
            # contradict that decision. Walking FAILED → PROCESSING → ...
            # → FINISHED is technically legal under VALID_TRANSITIONS, but
            # semantically wrong; refuse explicitly.
            if root.status in (TaskPhase.FAILED.value, TaskPhase.CANCELLED.value):
                logger.warning(
                    "[PIPELINE] Refusing to finalize CEO root {} from {} — pipeline completion conflicts with terminal failure/cancellation",
                    root.id, root.status,
                )
                return

            # Walk PROCESSING → COMPLETED → ACCEPTED → FINISHED, validating
            # each step. Skip steps the node is already past.
            target_chain = [
                TaskPhase.PROCESSING,
                TaskPhase.COMPLETED,
                TaskPhase.ACCEPTED,
                TaskPhase.FINISHED,
            ]
            for target in target_chain:
                if root.status == target.value:
                    continue
                current = TaskPhase(root.status)
                if not can_transition(current, target):
                    logger.warning(
                        "[PIPELINE] Cannot finalize CEO root {}: illegal transition {} → {} (skipping rest)",
                        root.id, current.value, target.value,
                    )
                    return
                root.set_status(target)

            # Synchronous save here on purpose: pipeline completion is a
            # rare, ordering-critical event. Async save would let external
            # readers see a stale tree between the in-memory mutation and
            # the background flush.
            tree.save(tree_path)
            logger.info(
                "[PIPELINE] Marked CEO root {} → FINISHED on pipeline completion",
                root.id,
            )
        except Exception as exc:  # pragma: no cover — defensive logging
            logger.warning("[PIPELINE] Failed to finalize CEO root on completion: {}", exc)
