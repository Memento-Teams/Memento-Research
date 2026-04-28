"""Memento v4 memory adapter.

Pipeline:
  Ingest:  finalize_session (causal edges) + ChromaDB (vector index)
           + [Opt 1] classify_memories + [Opt 2] detect_conflicts
  Recall:  [Stage 1] ChromaDB vector search (top_k × 3 candidates)
           [Stage 2] Hybrid rerank: vector + BM25 [Opt 4] + quoted + name + supersede [Opt 2]
           [Stage 3] Causal-chain BFS expansion (forward ≤5 hops, backward ≤2)
           [Stage 4] Build context: raw snippets + [Opt 5] MEMORY.md + causal edges + summaries
           [Stage 5] [Opt 3] Reflect synthesis (smart-routed — skipped for simple factuals)

Each of the 5 optimizations has an independent ablation flag; see ablation.py.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import tempfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

from .ablation import AblationFlags
from .bm25 import BM25Scorer
from .causal.finalize import finalize_session
from .causal.models import CausalEdge
from .causal.storage import (
    domain_memory_path,
    ensure_causal_memory_dir,
    find_session_node,
    load_all_edges,
    resolve_transcript_path,
)
from .llm import LLMClient
from .text_utils import (
    keyword_overlap,
    name_boost,
    person_names,
    quoted_boost,
    quoted_phrases,
    tokenize,
)
from .types import Conversation, Session


# ============================================================================
# Output type
# ============================================================================

@dataclass
class RecallContext:
    """Retrieved context + metadata returned to the caller."""
    raw_text: str = ""
    session_ids: list[str] = None  # type: ignore[assignment]
    metadata: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.session_ids is None:
            self.session_ids = []
        if self.metadata is None:
            self.metadata = {}


# ============================================================================
# Adapter interface (lightweight Protocol, not abc.ABC)
# ============================================================================

class MemoryAdapter(Protocol):
    """Minimal contract for a benchmark-compatible memory adapter."""

    name: str

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...
    async def ingest(self, conversation: Conversation, conv_id: str) -> None: ...
    async def recall(self, query: str, conv_id: str) -> RecallContext: ...
    async def reset(self, conv_id: str) -> None: ...


# ============================================================================
# Opt 1 — Memory classification prompts
# ============================================================================

_CLASSIFY_SYSTEM = """\
You classify pieces of information from a session summary into exactly three types.
Return JSON only. No markdown fences. No prose.

Types:
- "fact": verifiable objective information (e.g. "project uses PostgreSQL 15")
- "preference": subjective user preference or opinion (e.g. "user prefers tests first")
- "lesson": experience-based insight or learned pattern (e.g. "mocking DB causes CI failures")

Each item gets a confidence score 0.0-1.0 indicating how certain this classification is.
"""


def _build_classify_prompt(summary: dict) -> str:
    items: list[str] = []
    for f in summary.get("key_findings", []):
        items.append(f"- finding: {f}")
    for d in summary.get("key_decisions", []):
        if isinstance(d, dict):
            items.append(f"- decision: {d.get('decision', '')}")
        else:
            items.append(f"- decision: {d}")
    for e in summary.get("errors_lessons", []):
        items.append(f"- lesson: {e}")
    if not items:
        return ""
    return f"""\
Classify each item below into fact / preference / lesson with a confidence score.

Items:
{chr(10).join(items)}

Return JSON:
{{"classified": [{{"text": "original text", "type": "fact|preference|lesson", "confidence": 0.0}}]}}
"""


# ============================================================================
# Opt 2 — Conflict detection prompts
# ============================================================================

_CONFLICT_SYSTEM = """\
You detect factual contradictions between a new session and existing session facts.
Return JSON only. No markdown fences. No prose.
Only report genuine contradictions where the new information supersedes old information.
Do NOT flag things that are merely complementary or unrelated.
"""


def _build_conflict_prompt(
    new_session_id: str,
    new_facts: list[str],
    existing_facts: list[tuple[str, str]],
) -> str:
    if not new_facts or not existing_facts:
        return ""
    new_block = "\n".join(f"- {f}" for f in new_facts)
    existing_block = "\n".join(f"- [{sid}] {f}" for sid, f in existing_facts)
    return f"""\
Compare new session facts against existing facts. Identify genuine contradictions.

New session ({new_session_id}):
{new_block}

Existing facts:
{existing_block}

Return JSON:
{{"conflicts": [{{"new_fact": "...", "old_fact": "...", "old_session_id": "...", "explanation": "..."}}]}}
"""


# ============================================================================
# Opt 3 — Reflect synthesis prompts
# ============================================================================

_REFLECT_SYSTEM = """\
You synthesize retrieved memory sessions into a coherent, concise answer.
Focus on resolving contradictions and highlighting the most relevant information.
If sessions conflict, prefer the more recent one unless context suggests otherwise.
Be direct and factual.

Two distinct grounding rules — apply the correct one:

1) EVIDENCE PRESENT (answer directly):
   - The fact is stated in the sessions → answer concisely with EXACT wording
     from the transcript when possible (names, dates, numbers, proper nouns).
   - The fact can be INFERRED from what IS present using:
     a) session timestamps (e.g. "what did I have before Sept 2023");
     b) list membership (e.g. "before I got gravel bike, what other bikes");
     c) temporal ordering within a session;
     d) simple arithmetic on numbers present in the sessions.
   → In these cases, ANSWER — do NOT say "no information available".

2) EVIDENCE ABSENT (decline):
   - The specific entity asked about is never mentioned anywhere.
   - Completely unrelated topics only.
   → Reply exactly: "no information available"

Output rules:
- Plain prose only. No JSON, no markdown fences, no bullet points.
- Prefer to echo exact phrases from the transcript (helps downstream QA matching).
- For numeric answers, use Arabic numerals ("4 days" not "four days").
- For preference/suggestion queries, give a concrete list of the user's stated
  preferences, not a generic response.
- For simple factual queries (who/what/when/where/how many), answer in ≤1 sentence.
- For reasoning queries (why/how/compare), answer in ≤3 sentences.
"""


def _build_reflect_prompt(query: str, context: str) -> str:
    return f"""\
Based on the following retrieved memory sessions, answer the query.

Apply grounding rules from the system prompt:
- If the fact is stated OR can be inferred from timestamps / list-membership /
  arithmetic, answer directly (prefer exact transcript wording).
- Only reply "no information available" when the entity is never mentioned.

Query: {query}

Retrieved Memory:
{context}

Answer (plain prose, use exact phrasing from transcript when possible):
"""


def _is_simple_factual_query(query: str) -> bool:
    """Detect single-hop factual queries that don't need LLM synthesis.

    Reflect paraphrases evidence, which hurts F1 on questions where GT is a
    short proper-noun / number / date. For those we skip Reflect and let the
    raw-context QA copy verbatim wording.
    """
    q = query.strip().lower()
    if not q:
        return False

    # Reasoning keywords → always use Reflect
    reasoning_markers = (
        "why", "how come", "explain", "compare", "contrast", "summarize",
        "overall", "difference between", "trade-off", "tradeoff",
        "how does", "how do you", "what would happen", "what if",
        "how many days between", "how long between", "how long ago",
        "before ", "after ", "since ", "until ", "between ",
    )
    for m in reasoning_markers:
        if m in q:
            return False

    # Preference queries → use Reflect (needs long-form synthesis)
    preference_markers = (
        "suggest", "recommend", "what should i", "can you recommend",
        "what would you", "any ideas", "any tips", "any advice",
    )
    for m in preference_markers:
        if m in q:
            return False

    # Simple single-hop factuals → skip Reflect
    simple_starts = (
        "what is ", "what's ", "what was ", "what are ", "what were ",
        "what do ", "what does ", "what did ", "what kind ", "what type ",
        "who is ", "who's ", "who was ", "who are ", "who were ",
        "who did ", "who does ",
        "where is ", "where was ", "where are ", "where did ", "where does ",
        "when is ", "when was ", "when did ", "when will ", "when does ",
        "which ", "how many ", "how much ", "how often ",
        "name the ", "list the ", "tell me the ",
    )
    return any(q.startswith(p) for p in simple_starts)


# ============================================================================
# Helpers
# ============================================================================

def _build_raw_text(session: Session) -> str:
    """Reconstruct the verbatim session text (speaker attribution preserved)."""
    lines: list[str] = []
    if session.date_time:
        lines.append(f"[{session.date_time}]")
    for turn in session.turns:
        content = turn.text
        if turn.blip_caption:
            content += f" [image: {turn.blip_caption}]"
        lines.append(f'{turn.speaker} said, "{content}"')
    return "\n".join(lines)


def _session_to_messages(session: Session, conversation: Conversation) -> list[dict]:
    """Convert session turns into chat messages for storage."""
    messages: list[dict] = []
    if session.date_time:
        messages.append({
            "role": "system",
            "content": f"[This conversation took place on {session.date_time}]",
        })
    for turn in session.turns:
        role = "user" if turn.speaker == conversation.speaker_a else "assistant"
        content = turn.text
        if turn.blip_caption:
            content += f" [image: {turn.blip_caption}]"
        messages.append({"role": role, "content": content})
    return messages


def _write_raw_transcript(path: Path, messages: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(msg, ensure_ascii=False) for msg in messages]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _response_text(content: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts).strip()


def _extract_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for idx in range(start, len(text)):
        char = text[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(text[start: idx + 1])
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


# ============================================================================
# Adapter
# ============================================================================

class MemoryV4Adapter:
    """ChromaDB + BM25 hybrid + causal chain + 5 ablation-gated optimizations."""

    name = "memento_v4"

    def __init__(
        self,
        memory_root: Path,
        model: str = "gemini-3-flash-preview",
        provider: str = "openai",
        api_key: str | None = None,
        base_url: str | None = None,
        top_k: int = 20,
        ablation: AblationFlags | None = None,
        ingest_concurrency: int = 8,
    ):
        """
        Args:
            ingest_concurrency: number of concurrent LLM calls during ingest.
                Each session needs up to 3 LLM calls (finalize + classify +
                conflict detect). With 8-way parallelism, a 29-session conv
                drops from ~29 min (serial) to ~5-8 min.
        """
        self.memory_root = Path(memory_root)
        self.model = model
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url
        self.top_k = top_k
        self.ablation = ablation or AblationFlags()
        self.ingest_concurrency = ingest_concurrency
        self._client: LLMClient | None = None
        self._state: dict[str, dict] = {}
        # Serialises disk writes across worker threads during parallel ingest.
        self._storage_lock = Lock()

    # ── infra ───────────────────────────────────────────────────────────────

    def _get_client(self) -> LLMClient:
        if self._client is None:
            import os
            api_key = self.api_key or os.environ.get("OPENROUTER_API_KEY", "")
            base_url = (
                self.base_url
                or os.environ.get("OPENROUTER_BASE_URL")
                or "https://openrouter.ai/api/v1"
            )
            self._client = LLMClient(
                provider=self.provider, api_key=api_key, base_url=base_url,
            )
        return self._client

    def _memory_dir(self, conv_id: str) -> Path:
        return self.memory_root / f"conv_{conv_id}"

    def _sidecar_path(self, conv_id: str) -> Path:
        """JSON sidecar for v4-only metadata (classifications, supersede markers)."""
        return self._memory_dir(conv_id) / "_v4_meta.json"

    def _load_sidecar(self, conv_id: str) -> dict:
        path = self._sidecar_path(conv_id)
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"classifications": {}, "superseded": {}}

    def _save_sidecar(self, conv_id: str, data: dict) -> None:
        path = self._sidecar_path(conv_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    async def setup(self) -> None:
        self.memory_root.mkdir(parents=True, exist_ok=True)

    # ── INGEST ──────────────────────────────────────────────────────────────

    async def ingest(self, conversation: Conversation, conv_id: str) -> None:
        import chromadb

        memory_dir = self._memory_dir(conv_id)
        ensure_causal_memory_dir(memory_dir)
        client = self._get_client()

        # Per-conversation ChromaDB instance (lives in tempdir, reset cleans up).
        tmpdir = tempfile.mkdtemp(prefix="memento_v4_")
        chroma_client = chromadb.PersistentClient(path=str(Path(tmpdir) / "vec"))
        collection = chroma_client.create_collection(
            "sessions", metadata={"hnsw:space": "cosine"},
        )

        session_texts: dict[str, str] = {}
        sidecar = self._load_sidecar(conv_id)
        loop = asyncio.get_event_loop()

        # ─── Pass 1: write transcripts + finalize in PARALLEL ───────────────
        # Each worker thread writes its own transcript (unique path, safe) and
        # runs the finalize LLM call (slow, ~15-30s). The disk-write portion
        # inside finalize_session is protected by self._storage_lock so
        # shared files (DOMAIN_INDEX / edges / MEMORY.md) stay consistent.

        def _do_finalize(session: Session) -> tuple[int, str, str]:
            session_id = f"conv{conv_id}_sess{session.session_num}"
            raw_path = memory_dir / f"{session_id}.raw.jsonl"
            messages = _session_to_messages(session, conversation)
            _write_raw_transcript(raw_path, messages)
            raw_text = _build_raw_text(session)

            finalize_session(
                client=client,
                model=self.model,
                effort=None,
                memory_dir=memory_dir,
                cwd=f"/bench/conv{conv_id}",
                session_id=session_id,
                raw_transcript_path=raw_path,
                save_lock=self._storage_lock,
            )
            return session.session_num, session_id, raw_text

        with ThreadPoolExecutor(max_workers=self.ingest_concurrency) as pool:
            finalize_tasks = [
                loop.run_in_executor(pool, _do_finalize, s)
                for s in conversation.sessions
            ]
            finalize_results = await asyncio.gather(*finalize_tasks)

        # Now sequentially build ChromaDB / BM25 indexes from the (deterministic
        # session_num order ensures stable doc ordering).
        bm25 = BM25Scorer() if self.ablation.bm25_scoring else None
        doc_token_map: dict[str, int] = {}
        docs: list[str] = []
        ids: list[str] = []
        metas: list[dict] = []

        for session_num, session_id, raw_text in sorted(finalize_results):
            session_texts[session_id] = raw_text
            docs.append(raw_text)
            ids.append(f"doc_{session_num}")
            metas.append({"session_id": session_id})
            if bm25 is not None:
                tokens = tokenize(raw_text)
                doc_token_map[session_id] = len(bm25._doc_tokens)
                bm25.add_document(tokens)

        collection.add(documents=docs, ids=ids, metadatas=metas)

        # ─── Pass 2: Opt 1 classify + Opt 2 conflict in PARALLEL ────────────
        # Pre-compute each session's node + facts so parallel workers don't
        # contend on shared state. Conflict detection still sees only the
        # prior-session facts (by index), preserving sequential semantics.
        if self.ablation.classify_memories or self.ablation.conflict_detection:
            all_nodes: list[tuple[str, Any, list[str], dict]] = []
            for session in sorted(conversation.sessions, key=lambda s: s.session_num):
                session_id = f"conv{conv_id}_sess{session.session_num}"
                node = find_session_node(memory_dir, session_id)
                if node is None:
                    continue
                facts = list(node.key_findings) + [d.decision for d in node.key_decisions]
                summary = {
                    "key_findings": node.key_findings,
                    "key_decisions": [d.to_dict() for d in node.key_decisions],
                    "errors_lessons": node.errors_lessons,
                }
                all_nodes.append((session_id, node, facts, summary))

            def _do_classify_and_conflict(
                idx: int,
                session_id: str,
                facts: list[str],
                summary: dict,
            ) -> tuple[str, list[dict], list[dict]]:
                classified: list[dict] = []
                conflicts: list[dict] = []
                if self.ablation.classify_memories:
                    classified = self._classify_memories(summary)
                if self.ablation.conflict_detection and idx > 0:
                    prior_facts = [
                        (sid, f)
                        for sid, _, fs, _ in all_nodes[:idx]
                        for f in fs
                    ]
                    if prior_facts and facts:
                        conflicts = self._detect_conflicts(
                            session_id, facts, prior_facts,
                        )
                return session_id, classified, conflicts

            with ThreadPoolExecutor(max_workers=self.ingest_concurrency) as pool:
                cc_tasks = [
                    loop.run_in_executor(
                        pool, _do_classify_and_conflict,
                        i, sid, facts, summary,
                    )
                    for i, (sid, _, facts, summary) in enumerate(all_nodes)
                ]
                cc_results = await asyncio.gather(*cc_tasks)

            for session_id, classified, conflicts in cc_results:
                if classified:
                    sidecar["classifications"][session_id] = classified
                for c in conflicts:
                    old_sid = c.get("old_session_id", "")
                    if old_sid:
                        sidecar["superseded"].setdefault(old_sid, []).append({
                            "superseded_by": session_id,
                            "old_fact": c.get("old_fact", ""),
                            "new_fact": c.get("new_fact", ""),
                            "explanation": c.get("explanation", ""),
                        })

            self._save_sidecar(conv_id, sidecar)

        self._state[conv_id] = {
            "tmpdir": tmpdir,
            "collection": collection,
            "session_texts": session_texts,
            "bm25": bm25,
            "doc_token_map": doc_token_map,
        }

    # ── Opt 1: classify ─────────────────────────────────────────────────────

    def _classify_memories(self, summary: dict) -> list[dict]:
        prompt = _build_classify_prompt(summary)
        if not prompt:
            return []
        try:
            resp = self._get_client().create_message(
                model=self.model, max_tokens=2048, effort=None,
                system=_CLASSIFY_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            payload = _extract_json_object(_response_text(resp.content))
            if payload and isinstance(payload.get("classified"), list):
                return payload["classified"]
        except Exception:
            pass
        return []

    # ── Opt 2: conflict detection ───────────────────────────────────────────

    def _detect_conflicts(
        self,
        new_session_id: str,
        new_facts: list[str],
        existing_facts: list[tuple[str, str]],
    ) -> list[dict]:
        prompt = _build_conflict_prompt(new_session_id, new_facts, existing_facts)
        if not prompt:
            return []
        try:
            resp = self._get_client().create_message(
                model=self.model, max_tokens=2048, effort=None,
                system=_CONFLICT_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            payload = _extract_json_object(_response_text(resp.content))
            if payload and isinstance(payload.get("conflicts"), list):
                return payload["conflicts"]
        except Exception:
            pass
        return []

    # ── Opt 3: reflect synthesis ────────────────────────────────────────────

    def _reflect(self, query: str, context: str) -> str:
        try:
            resp = self._get_client().create_message(
                model=self.model, max_tokens=2048, effort=None,
                system=_REFLECT_SYSTEM,
                messages=[{"role": "user", "content": _build_reflect_prompt(query, context)}],
            )
            return _response_text(resp.content)
        except Exception:
            return ""

    # ── RECALL ──────────────────────────────────────────────────────────────

    async def recall(self, query: str, conv_id: str) -> RecallContext:
        state = self._state.get(conv_id)
        memory_dir = self._memory_dir(conv_id)

        if not state:
            return RecallContext(raw_text="", metadata={"error": "not ingested"})

        collection = state["collection"]
        session_texts = state["session_texts"]
        bm25: BM25Scorer | None = state.get("bm25")
        doc_token_map: dict[str, int] = state.get("doc_token_map", {})

        trace: dict[str, Any] = {"flags": self.ablation.to_dict()}

        # ── Stage 1: vector search ──────────────────────────────────────────
        n_retrieve = min(self.top_k * 3, collection.count())
        results = collection.query(
            query_texts=[query], n_results=n_retrieve,
            include=["distances", "metadatas", "documents"],
        )
        raw_ids = [m["session_id"] for m in results["metadatas"][0]]
        raw_distances = results["distances"][0]
        raw_docs = results["documents"][0]

        # ── Stage 2: hybrid rerank ──────────────────────────────────────────
        names = person_names(query)
        name_words = {n.lower() for n in names}
        all_kws = tokenize(query)
        predicate_kws = [w for w in all_kws if w not in name_words]
        quoted = quoted_phrases(query)

        bm25_scores: dict[str, float] = {}
        if self.ablation.bm25_scoring and bm25 is not None:
            query_tokens = tokenize(query)
            all_bm25 = bm25.score_all(query_tokens)
            max_bm25 = max(all_bm25) if all_bm25 and max(all_bm25) > 0 else 1.0
            for sid, doc_idx in doc_token_map.items():
                bm25_scores[sid] = all_bm25[doc_idx] / max_bm25
            trace["bm25_max_raw"] = max_bm25

        sidecar = self._load_sidecar(conv_id) if self.ablation.conflict_detection else {}
        superseded_map: dict[str, list] = sidecar.get("superseded", {})
        classifications_map: dict[str, list] = sidecar.get("classifications", {})

        scored: list[tuple[str, str, float]] = []
        for sid, dist, doc in zip(raw_ids, raw_distances, raw_docs):
            if self.ablation.bm25_scoring and sid in bm25_scores:
                # Weighted additive fusion (0.45 vector + 0.55 BM25).
                vec_sim = max(0.0, 1.0 - dist / 2.0)
                bm25_norm = bm25_scores[sid]
                fused = 1.0 - (0.45 * vec_sim + 0.55 * bm25_norm)
            else:
                fused = dist * (1.0 - 0.50 * keyword_overlap(predicate_kws, doc))

            qb = quoted_boost(quoted, doc)
            if qb > 0:
                fused *= (1.0 - 0.60 * qb)
            nb = name_boost(names, doc)
            if nb > 0:
                fused *= (1.0 - 0.20 * nb)

            if self.ablation.conflict_detection and sid in superseded_map:
                penalty = min(0.30 * len(superseded_map[sid]), 0.60)
                fused *= (1.0 + penalty)

            scored.append((sid, doc, fused))

        scored.sort(key=lambda x: x[2])
        seed_ids = [sid for sid, _, _ in scored[: self.top_k // 2]]
        trace["scored_top5"] = [{"sid": s, "fused": round(f, 4)} for s, _, f in scored[:5]]

        # ── Stage 3: causal-chain BFS expansion ─────────────────────────────
        all_edges = load_all_edges(memory_dir)
        outgoing: dict[str, list[CausalEdge]] = {}
        incoming: dict[str, list[CausalEdge]] = {}
        for edge in all_edges:
            outgoing.setdefault(edge.source_session, []).append(edge)
            incoming.setdefault(edge.target_session, []).append(edge)

        expanded_ids: set[str] = set(seed_ids)
        expansion_reason: dict[str, CausalEdge] = {}

        bq: deque[tuple[str, int]] = deque((sid, 0) for sid in seed_ids)
        while bq:
            sid, depth = bq.popleft()
            if depth >= 5:
                continue
            for edge in outgoing.get(sid, []):
                if edge.target_session not in expanded_ids:
                    expanded_ids.add(edge.target_session)
                    expansion_reason[edge.target_session] = edge
                    bq.append((edge.target_session, depth + 1))

        fq: deque[tuple[str, int]] = deque((sid, 0) for sid in seed_ids)
        while fq:
            sid, depth = fq.popleft()
            if depth >= 2:
                continue
            for edge in incoming.get(sid, []):
                if edge.source_session not in expanded_ids:
                    expanded_ids.add(edge.source_session)
                    expansion_reason[edge.source_session] = edge
                    fq.append((edge.source_session, depth + 1))

        # ── Stage 4: build context ──────────────────────────────────────────
        ordered = list(seed_ids)
        for sid in expanded_ids:
            if sid not in ordered:
                ordered.append(sid)

        output_set = set(ordered[: self.top_k])
        relevant_edges = [
            e for e in all_edges
            if e.source_session in output_set and e.target_session in output_set
        ]

        lines: list[str] = ["# Memento v4 — Causal Session Memory", ""]

        # P0-1: raw snippets first (MemPalace-style exact-wording match).
        lines.append("## Raw Session Snippets (authoritative, use for exact wording)")
        lines.append("")
        for sid in ordered[: self.top_k]:
            if sid in session_texts:
                lines.append(f"### {sid}")
                lines.append(session_texts[sid])
                lines.append("")
        lines.append("---")
        lines.append("")

        # Opt 5: domain MEMORY.md hot summary.
        memory_summary_included = False
        if self.ablation.memory_summary:
            domains_seen: set[str] = set()
            for sid in ordered[: self.top_k]:
                node = find_session_node(memory_dir, sid)
                if node and node.domain and node.domain not in domains_seen:
                    domains_seen.add(node.domain)
                    md_path = domain_memory_path(memory_dir, node.domain)
                    if md_path.exists():
                        md = md_path.read_text(encoding="utf-8").strip()
                        if md and "No sessions yet" not in md:
                            lines.append(f"## Domain Summary: {node.domain}")
                            lines.append(md)
                            lines.append("")
                            memory_summary_included = True
            trace["memory_summary_domains"] = list(domains_seen)

        # Causal graph.
        if relevant_edges:
            lines.append("## Causal Relationships")
            for edge in relevant_edges:
                src = find_session_node(memory_dir, edge.source_session)
                tgt = find_session_node(memory_dir, edge.target_session)
                src_label = src.title if src else edge.source_session
                tgt_label = tgt.title if tgt else edge.target_session
                lines.append(
                    f"- [{src_label}] --{edge.relation}--> [{tgt_label}]: {edge.evidence}"
                )
            lines.append("")

        # Session details (summary + classification tags + supersede markers).
        for sid in ordered[: self.top_k]:
            node = find_session_node(memory_dir, sid)
            marker = "seed" if sid in seed_ids else "linked"
            supersede_tag = ""
            if self.ablation.conflict_detection and sid in superseded_map:
                supersede_tag = " [SUPERSEDED]"

            if node:
                lines.append(f"## {node.title} ({node.outcome}, {marker}){supersede_tag}")
                if node.goal:
                    lines.append(f"Goal: {node.goal}")
                if node.key_findings:
                    for finding in node.key_findings[:3]:
                        tag = self._classify_tag(sid, finding, classifications_map)
                        lines.append(f"- {finding}{tag}")
                if node.errors_lessons:
                    for lesson in node.errors_lessons[:2]:
                        tag = self._classify_tag(sid, lesson, classifications_map)
                        lines.append(f"- {lesson}{tag}")
                if self.ablation.conflict_detection and sid in superseded_map:
                    for s_info in superseded_map[sid][:2]:
                        lines.append(
                            f"  [Superseded by {s_info['superseded_by']}: "
                            f"{s_info.get('explanation', '')}]"
                        )
            else:
                lines.append(f"## {sid} ({marker}){supersede_tag}")

            if sid in expansion_reason:
                edge = expansion_reason[sid]
                lines.append(f"[Linked via '{edge.relation}': {edge.evidence}]")

            # Full transcript (JSONL replay).
            tpath = resolve_transcript_path(memory_dir, node) if node else None
            if tpath and tpath.exists():
                try:
                    lines.append("")
                    lines.append("Conversation:")
                    for raw_line in tpath.read_text(encoding="utf-8").splitlines():
                        raw_line = raw_line.strip()
                        if not raw_line:
                            continue
                        msg = json.loads(raw_line)
                        role = msg.get("role", "?")
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            content = " ".join(
                                b.get("text", "") for b in content
                                if isinstance(b, dict) and b.get("type") == "text"
                            )
                        lines.append(f"  {role}: {content}")
                except Exception:
                    pass
            elif sid in session_texts:
                lines.append("")
                lines.append(session_texts[sid])
            lines.append("")

        section = "\n".join(lines)

        # ── Stage 5: Opt 3 reflect synthesis (with routing) ─────────────────
        reflect_text = ""
        reflect_skipped_reason: str | None = None
        if self.ablation.reflect_synthesis:
            if _is_simple_factual_query(query):
                reflect_skipped_reason = "simple_factual_query"
            else:
                reflect_text = self._reflect(query, section)
                if reflect_text:
                    section = (
                        "# Synthesized Answer (Reflect)\n\n"
                        + reflect_text
                        + "\n\n---\n\n"
                        + section
                    )
                    trace["reflect_len"] = len(reflect_text)

        trace["reflect_skipped_reason"] = reflect_skipped_reason
        trace["reflect_used"] = bool(reflect_text)
        trace["superseded_sessions"] = list(superseded_map.keys())
        trace["memory_summary_included"] = memory_summary_included

        return RecallContext(
            raw_text=section,
            session_ids=ordered[: self.top_k],
            metadata={
                "seed_ids": seed_ids,
                "expanded_count": len(expanded_ids),
                "causal_edges_total": len(all_edges),
                "causal_edges_in_context": len(relevant_edges),
                "ablation": trace,
            },
        )

    # ── helpers ─────────────────────────────────────────────────────────────

    def _classify_tag(
        self,
        sid: str,
        text: str,
        classifications_map: dict[str, list],
    ) -> str:
        if not self.ablation.classify_memories or sid not in classifications_map:
            return ""
        for c in classifications_map[sid]:
            if c.get("text", "").strip() == text.strip():
                return f" [{c['type']}:{c.get('confidence', '?')}]"
        return ""

    # ── lifecycle ───────────────────────────────────────────────────────────

    async def reset(self, conv_id: str) -> None:
        memory_dir = self._memory_dir(conv_id)
        if memory_dir.exists():
            shutil.rmtree(memory_dir)
        state = self._state.pop(conv_id, None)
        if state:
            shutil.rmtree(state["tmpdir"], ignore_errors=True)

    async def teardown(self) -> None:
        for conv_id in list(self._state.keys()):
            await self.reset(conv_id)
