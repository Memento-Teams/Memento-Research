from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..llm import LLMClient
from .models import CausalEdge, DecisionRecord, SessionNode, TranscriptRef

# Rough character-to-token ratio (replaces cc-mini/core/compact import)
CHARS_PER_TOKEN = 4
from .storage import (
    domain_slug,
    ensure_causal_memory_dir,
    find_session_node,
    list_recent_sessions,
    needs_finalization,
    save_session_node,
    update_session_links,
    upsert_edges,
)

_VALID_OUTCOMES = {"completed", "partial", "blocked", "abandoned"}
_VALID_RELATIONS = {"continues", "resolves", "contradicts", "relates"}
_MAX_TRANSCRIPT_LINE_CHARS = 220
_MAX_TRANSCRIPT_LINES = 180
_MAX_CANDIDATE_SESSIONS = 16

_FINALIZE_SYSTEM = """\
You convert a session transcript into structured causal memory.

Return exactly one JSON object. No markdown fences. No prose outside JSON.
Keep the summary factual and compact. Prefer stable history over chatter.

CRITICAL rules for key_quotes (this is the #1 lever for downstream retrieval):
- Extract 5-12 verbatim quotes from the transcript. More quotes is better
  than fewer, as long as each quote carries a distinct fact.
- Copy the EXACT words from the transcript. Do NOT paraphrase, summarize,
  or rephrase. Preserve original punctuation and casing.
- MUST include every occurrence of:
    * specific numbers (counts, prices, durations, scores, dates, years, ages)
    * proper nouns (people, places, brands, products, organizations, songs,
      books, movies, pets' names)
    * explicit time/date references (yesterday, last Tuesday, June 2023, etc.)
    * lists of items the user owns/did/visited/bought
    * preferences expressed with "I like / I prefer / I hate / I love"
    * negations ("I don't ...", "I never ...", "not a fan of ...")
- Include the speaker (user/assistant) and the turn number.
- Quotes are what downstream retrieval matches against — treat them as the
  authoritative fact index for this session.

Allowed outcome values: completed, partial, blocked, abandoned.
Allowed relation values: continues, resolves, contradicts, relates.
Only reference target_session values that appear in the provided candidate list.
If no candidate sessions are relevant, return an empty causal_edges list.
Choose an existing domain unless the transcript is clearly about a new area.
"""


def finalize_session(
    *,
    client: LLMClient,
    model: str,
    effort: str | None,
    memory_dir: Path,
    cwd: str,
    session_id: str,
    raw_transcript_path: Path,
    save_lock: Any = None,
) -> SessionNode | None:
    """Summarise a session via LLM and persist it.

    When called from multiple threads (parallel ingest), pass a
    `threading.Lock` as *save_lock* to serialize the disk-write portion
    (which updates shared files: DOMAIN_INDEX.json, edges.json, MEMORY.md).
    The LLM call itself runs concurrently.
    """
    ensure_causal_memory_dir(memory_dir)
    if not needs_finalization(memory_dir, session_id, raw_transcript_path):
        return find_session_node(memory_dir, session_id)

    raw_messages = _load_raw_messages(raw_transcript_path)
    if not raw_messages:
        return None

    transcript_lines = _build_transcript_lines(raw_messages)
    transcript_text = _limit_transcript_lines(transcript_lines)
    transcript_turns = len(transcript_lines)
    transcript_tokens = max(len(transcript_text) // CHARS_PER_TOKEN, 1)

    candidates = list_recent_sessions(
        memory_dir,
        limit=_MAX_CANDIDATE_SESSIONS,
        exclude_session_ids={session_id},
    )
    existing = find_session_node(memory_dir, session_id)

    prompt = _build_finalize_prompt(
        cwd=cwd,
        session_id=session_id,
        transcript_text=transcript_text,
        transcript_turns=transcript_turns,
        transcript_tokens=transcript_tokens,
        candidates=candidates,
        current_domain=existing.domain if existing else "",
    )

    payload: dict[str, Any] | None = None
    try:
        response = client.create_message(
            model=model,
            max_tokens=3000,
            system=_FINALIZE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            effort=effort,
        )
        response_text = _response_text(response.content)
        payload = _extract_json_object(response_text)
    except Exception:
        payload = None

    if payload is None:
        payload = _fallback_payload(raw_messages, cwd, session_id)

    session_node, edges, domain_description = _payload_to_artifacts(
        payload=payload,
        cwd=cwd,
        session_id=session_id,
        transcript_turns=transcript_turns,
        transcript_tokens=transcript_tokens,
        candidate_sessions=candidates,
        existing=existing,
    )

    # Serialise disk writes across threads (shared DOMAIN_INDEX + edges + MEMORY.md).
    # LLM work above ran concurrently; only the cheap disk update is blocking.
    from contextlib import nullcontext
    ctx = save_lock if save_lock is not None else nullcontext()
    with ctx:
        saved = save_session_node(
            memory_dir,
            session_node,
            transcript_source_path=raw_transcript_path,
            domain_description=domain_description,
        )
        if edges:
            upsert_edges(memory_dir, edges)
            update_session_links(
                memory_dir,
                saved.session_id,
                [edge.target_session for edge in edges if edge.relation in {"continues", "resolves", "contradicts"}],
            )
    return saved


def _load_raw_messages(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    out.append(data)
    except OSError:
        return []
    return out


def _build_transcript_lines(messages: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for idx, msg in enumerate(messages, start=1):
        role = str(msg.get("role", "unknown"))
        content = _summarize_content(msg.get("content", ""))
        lines.append(f"{idx:03d} | {role} | {content}")
    return lines


def _summarize_content(content: Any) -> str:
    if isinstance(content, str):
        return _truncate(_compact_ws(content))
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                btype = block.get("type", "")
                if btype == "text":
                    parts.append(_truncate(_compact_ws(str(block.get("text", "")))))
                elif btype == "tool_use":
                    name = str(block.get("name", "")).strip()
                    payload = json.dumps(block.get("input", {}), ensure_ascii=False)
                    parts.append(_truncate(f"tool_use {name} {payload}"))
                elif btype == "tool_result":
                    result_text = block.get("content", "")
                    if not isinstance(result_text, str):
                        result_text = json.dumps(result_text, ensure_ascii=False)
                    parts.append(_truncate(f"tool_result {result_text}"))
                elif btype == "image":
                    parts.append("[image]")
                else:
                    parts.append(_truncate(json.dumps(block, ensure_ascii=False)))
            else:
                parts.append(_truncate(_compact_ws(str(block))))
        return " || ".join(part for part in parts if part) or "(empty)"
    return _truncate(_compact_ws(str(content)))


def _truncate(text: str, limit: int = _MAX_TRANSCRIPT_LINE_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _compact_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _limit_transcript_lines(lines: list[str]) -> str:
    if len(lines) <= _MAX_TRANSCRIPT_LINES:
        return "\n".join(lines)
    head = lines[:120]
    tail = lines[-40:]
    return "\n".join(head + ["...", f"... omitted {len(lines) - 160} lines ...", "..."] + tail)


def _candidate_block(node: SessionNode) -> str:
    decisions = "; ".join(item.decision for item in node.key_decisions[:2])
    findings = "; ".join(node.key_findings[:2])
    open_questions = "; ".join(node.open_questions[:2])
    keywords = ", ".join(node.keywords[:6])
    return (
        f"- {node.session_id} | domain={node.domain} | outcome={node.outcome} | title={node.title}\n"
        f"  goal={node.goal}\n"
        f"  decisions={decisions}\n"
        f"  findings={findings}\n"
        f"  open_questions={open_questions}\n"
        f"  keywords={keywords}"
    )


def _build_finalize_prompt(
    *,
    cwd: str,
    session_id: str,
    transcript_text: str,
    transcript_turns: int,
    transcript_tokens: int,
    candidates: list[SessionNode],
    current_domain: str,
) -> str:
    candidate_text = "\n".join(_candidate_block(node) for node in candidates) or "- none"
    current_domain_line = current_domain or "none"
    cwd_name = Path(cwd).name or "general"
    return f"""\
Summarize this coding-agent session into causal memory JSON.

Session metadata:
- session_id: {session_id}
- cwd: {cwd}
- cwd_basename: {cwd_name}
- current_domain_if_any: {current_domain_line}
- transcript_turns: {transcript_turns}
- transcript_tokens_approx: {transcript_tokens}

JSON schema:
{{
  "summary": {{
    "title": "short session title",
    "goal": "what the user was trying to accomplish",
    "outcome": "completed|partial|blocked|abandoned",
    "key_decisions": [
      {{
        "decision": "decision text",
        "reason": "why it was made",
        "alternatives": ["optional"],
        "confidence": 0.0,
        "evidence_turns": [1, 2]
      }}
    ],
    "key_findings": ["important technical findings"],
    "errors_lessons": ["important errors or lessons"],
    "open_questions": ["unresolved items"],
    "files_touched": ["relative/or absolute path when visible"],
    "keywords": ["retrieval keywords"],
    "key_quotes": [
      {{"speaker": "user", "turn": 3, "quote": "exact verbatim quote from transcript"}},
      {{"speaker": "assistant", "turn": 4, "quote": "exact verbatim quote"}}
    ]
  }},
  "domain": {{
    "action": "existing|new",
    "name": "domain slug or proposed new domain",
    "description": "one line domain description"
  }},
  "causal_edges": [
    {{
      "target_session": "one candidate session id",
      "relation": "continues|resolves|contradicts|relates",
      "evidence": "one sentence explaining the link"
    }}
  ]
}}

Candidate prior sessions:
{candidate_text}

Transcript digest:
{transcript_text}
"""


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
                chunk = text[start : idx + 1]
                try:
                    parsed = json.loads(chunk)
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def _fallback_payload(messages: list[dict[str, Any]], cwd: str, session_id: str) -> dict[str, Any]:
    first_user = ""
    last_assistant = ""
    files_touched = _extract_files_from_messages(messages)
    for msg in messages:
        if not first_user and msg.get("role") == "user":
            first_user = _summarize_content(msg.get("content", ""))
        if msg.get("role") == "assistant":
            last_assistant = _summarize_content(msg.get("content", ""))
    title = first_user[:80] if first_user else f"Session {session_id[:8]}"
    goal = first_user or "Continue project work"
    findings = [last_assistant] if last_assistant else []
    keywords = _heuristic_keywords(" ".join(filter(None, [first_user, last_assistant])))
    return {
        "summary": {
            "title": title,
            "goal": goal,
            "outcome": "partial",
            "key_decisions": [],
            "key_findings": findings[:3],
            "errors_lessons": [],
            "open_questions": [],
            "files_touched": files_touched[:12],
            "keywords": keywords[:12],
        },
        "domain": {
            "action": "new",
            "name": domain_slug(Path(cwd).name),
            "description": f"Work related to {Path(cwd).name or 'general tasks'}",
        },
        "causal_edges": [],
    }


def _extract_files_from_messages(messages: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for msg in messages:
        content = msg.get("content", "")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            tool_name = str(block.get("name", ""))
            if tool_name not in {"Read", "Edit", "Write"}:
                continue
            path = str((block.get("input", {}) or {}).get("file_path", "")).strip()
            if path and path not in out:
                out.append(path)
    return out


def _heuristic_keywords(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9_./-]{3,}", text.lower())
    stop = {"the", "this", "that", "with", "from", "have", "need", "what", "when", "where", "help", "please"}
    seen: set[str] = set()
    out: list[str] = []
    for word in words:
        if word in stop or word.isdigit():
            continue
        if word not in seen:
            seen.add(word)
            out.append(word)
    return out


def _payload_to_artifacts(
    *,
    payload: dict[str, Any],
    cwd: str,
    session_id: str,
    transcript_turns: int,
    transcript_tokens: int,
    candidate_sessions: list[SessionNode],
    existing: SessionNode | None,
) -> tuple[SessionNode, list[CausalEdge], str]:
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    domain_info = payload.get("domain", {}) if isinstance(payload.get("domain"), dict) else {}
    domain_name = _normalize_domain_name(
        domain_info.get("name", ""),
        cwd=cwd,
        existing=existing.domain if existing else "",
        candidates=candidate_sessions,
    )
    domain_description = str(domain_info.get("description", "")).strip()
    title = str(summary.get("title", "")).strip() or (existing.title if existing else "") or f"Session {session_id[:8]}"
    goal = str(summary.get("goal", "")).strip() or (existing.goal if existing else "") or title
    outcome = str(summary.get("outcome", "partial") or "partial").strip().lower()
    if outcome not in _VALID_OUTCOMES:
        outcome = "partial"
    key_decisions = _normalize_decisions(summary.get("key_decisions"))
    files_touched = _clean_strings(summary.get("files_touched"))
    keywords = _clean_strings(summary.get("keywords"))
    if not keywords:
        keywords = _heuristic_keywords(" ".join([title, goal] + files_touched))
    transcript_ref = TranscriptRef(
        file=existing.transcript_ref.file if existing else "",
        turns=transcript_turns,
        tokens_approx=transcript_tokens,
    )

    node = SessionNode(
        session_id=session_id,
        domain=domain_name,
        title=title,
        goal=goal,
        outcome=outcome,
        key_decisions=key_decisions,
        key_findings=_clean_strings(summary.get("key_findings")),
        errors_lessons=_clean_strings(summary.get("errors_lessons")),
        open_questions=_clean_strings(summary.get("open_questions")),
        files_touched=files_touched[:20],
        keywords=keywords[:20],
        key_quotes=_normalize_quotes(summary.get("key_quotes")),
        transcript_ref=transcript_ref,
        artifacts=existing.artifacts if existing else [],
        continues_from=list(existing.continues_from) if existing else [],
        continued_by=list(existing.continued_by) if existing else [],
        cwd=cwd,
        created_at=existing.created_at if existing else "",
        updated_at=existing.updated_at if existing else "",
        last_accessed=existing.last_accessed if existing else "",
        access_count=existing.access_count if existing else 0,
    )

    candidate_map = {item.session_id: item for item in candidate_sessions}
    edges: list[CausalEdge] = []
    for raw_edge in payload.get("causal_edges", []):
        if not isinstance(raw_edge, dict):
            continue
        target_session = str(raw_edge.get("target_session", "")).strip()
        relation = str(raw_edge.get("relation", "")).strip().lower()
        if target_session not in candidate_map or relation not in _VALID_RELATIONS:
            continue
        target_node = candidate_map[target_session]
        edges.append(
            CausalEdge(
                source_session=session_id,
                target_session=target_session,
                relation=relation,
                evidence=str(raw_edge.get("evidence", "")).strip(),
                source_domain=node.domain,
                target_domain=target_node.domain,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        )

    return node, edges, domain_description


def _normalize_domain_name(
    raw_name: Any,
    *,
    cwd: str,
    existing: str,
    candidates: list[SessionNode],
) -> str:
    text = domain_slug(str(raw_name or "").strip())
    if text:
        candidate_domains = {item.domain for item in candidates}
        if text in candidate_domains:
            return text
        if existing:
            if text == domain_slug(existing):
                return domain_slug(existing)
        return text
    if existing:
        return domain_slug(existing)
    return domain_slug(Path(cwd).name or "general")


def _normalize_decisions(value: Any) -> list[DecisionRecord]:
    if not isinstance(value, list):
        return []
    out: list[DecisionRecord] = []
    for item in value:
        if isinstance(item, dict):
            decision = DecisionRecord.from_dict(item)
            if decision.decision:
                out.append(decision)
        elif isinstance(item, str) and item.strip():
            out.append(DecisionRecord(decision=item.strip()))
    return out[:8]


def _normalize_quotes(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    out: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote", "")).strip()
        if not quote:
            continue
        speaker = str(item.get("speaker", "")).strip()
        try:
            turn = int(item.get("turn", 0))
        except (TypeError, ValueError):
            turn = 0
        out.append({"speaker": speaker, "turn": turn, "quote": quote})
    return out[:12]


def _clean_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        if text not in seen:
            seen.add(text)
            out.append(text)
    return out
