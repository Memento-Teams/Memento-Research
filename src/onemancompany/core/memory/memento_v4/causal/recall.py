from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from .models import CausalEdge, DomainRecord, SessionNode
from .storage import (
    find_session_node,
    load_all_edges,
    load_domain_index,
    load_domain_sessions,
    mark_sessions_accessed,
    resolve_transcript_path,
)


@dataclass
class RecallResult:
    section: str = ""
    session_ids: list[str] = field(default_factory=list)
    routed_domains: list[str] = field(default_factory=list)


def build_recall_result(
    *,
    memory_dir: Path,
    cwd: str,
    query: str,
    exclude_session_id: str | None = None,
) -> RecallResult:
    domains = route_domains(memory_dir=memory_dir, cwd=cwd, query=query, limit=2)
    if not domains:
        return RecallResult()

    seeds: list[SessionNode] = []
    seen_seed_ids: set[str] = set()
    for domain in domains:
        for node in match_sessions(memory_dir, domain.slug, query, limit=2):
            if exclude_session_id and node.session_id == exclude_session_id:
                continue
            if node.session_id in seen_seed_ids:
                continue
            seen_seed_ids.add(node.session_id)
            seeds.append(node)

    if not seeds:
        return RecallResult()

    edges = load_all_edges(memory_dir)
    expanded = expand_causal_chain(
        memory_dir=memory_dir,
        seeds=seeds,
        edges=edges,
        exclude_session_id=exclude_session_id,
    )
    if not expanded:
        return RecallResult()

    section = build_recall_section(memory_dir, query, domains, seeds, expanded, edges)
    if not section:
        return RecallResult()

    session_ids = [node.session_id for node in expanded]
    return RecallResult(
        section=section,
        session_ids=session_ids,
        routed_domains=[domain.slug for domain in domains],
    )


def route_domains(
    *,
    memory_dir: Path,
    cwd: str,
    query: str,
    limit: int = 2,
) -> list[DomainRecord]:
    all_domains = load_domain_index(memory_dir)
    if not all_domains:
        return []

    query_terms = _terms(query)
    cwd_parts = _terms(cwd) + _terms(Path(cwd).name)
    scores: list[tuple[float, DomainRecord]] = []
    for domain in all_domains:
        score = 0.0
        domain_terms = _terms(domain.slug) + _terms(domain.name)
        for hint in domain.cwd_hints:
            if hint and (cwd.startswith(hint) or hint.startswith(cwd)):
                score += 6.0
            hint_name = Path(hint).name
            if hint_name and hint_name.lower() == Path(cwd).name.lower():
                score += 4.0
        for term in query_terms:
            if term in domain_terms:
                score += 3.5
            if term in [item.lower() for item in domain.keywords]:
                score += 2.5
            if term in (domain.description or "").lower():
                score += 1.5
        for term in cwd_parts:
            if term in domain_terms:
                score += 2.0
        if domain.session_count > 0:
            score += min(domain.session_count, 5) * 0.1
        if score > 0:
            scores.append((score, domain))

    scores.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in scores[:limit]]


def match_sessions(memory_dir: Path, domain_name: str, query: str, limit: int = 2) -> list[SessionNode]:
    nodes = load_domain_sessions(memory_dir, domain_name)
    if not nodes:
        return []
    query_terms = _terms(query)
    if not query_terms:
        return nodes[:limit]

    scored: list[tuple[float, SessionNode]] = []
    for node in nodes:
        score = _score_node(node, query_terms)
        if score > 0:
            scored.append((score, node))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in scored[:limit]]


def _score_node(node: SessionNode, query_terms: list[str]) -> float:
    haystack = node.search_blob().lower()
    score = 0.0
    title = node.title.lower()
    goal = node.goal.lower()
    files = " ".join(node.files_touched).lower()
    keywords = " ".join(node.keywords).lower()
    for term in query_terms:
        if term in title:
            score += 5.0
        if term in goal:
            score += 4.0
        if term in files:
            score += 5.0
        if term in keywords:
            score += 4.0
        if term in haystack:
            score += 1.2
    score += min(node.access_count, 10) * 0.05
    return score


def expand_causal_chain(
    *,
    memory_dir: Path,
    seeds: list[SessionNode],
    edges: list[CausalEdge],
    exclude_session_id: str | None = None,
) -> list[SessionNode]:
    nodes: dict[str, SessionNode] = {node.session_id: node for node in seeds}
    outgoing: dict[str, list[CausalEdge]] = {}
    incoming: dict[str, list[CausalEdge]] = {}
    for edge in edges:
        outgoing.setdefault(edge.source_session, []).append(edge)
        incoming.setdefault(edge.target_session, []).append(edge)

    backward_q = deque((node.session_id, 0) for node in seeds)
    while backward_q:
        session_id, depth = backward_q.popleft()
        if depth >= 5:
            continue
        for edge in outgoing.get(session_id, []):
            if edge.target_session == exclude_session_id:
                continue
            if edge.target_session in nodes:
                continue
            node = find_session_node(memory_dir, edge.target_session)
            if node is None:
                continue
            nodes[node.session_id] = node
            backward_q.append((node.session_id, depth + 1))

    forward_q = deque((node.session_id, 0) for node in seeds)
    while forward_q:
        session_id, depth = forward_q.popleft()
        if depth >= 2:
            continue
        for edge in incoming.get(session_id, []):
            if edge.source_session == exclude_session_id:
                continue
            if edge.source_session in nodes:
                continue
            node = find_session_node(memory_dir, edge.source_session)
            if node is None:
                continue
            nodes[node.session_id] = node
            forward_q.append((node.session_id, depth + 1))

    ordered_ids: list[str] = []
    for node in seeds:
        ordered_ids.append(node.session_id)
        for edge in outgoing.get(node.session_id, []):
            if edge.target_session in nodes and edge.target_session not in ordered_ids:
                ordered_ids.append(edge.target_session)
        for edge in incoming.get(node.session_id, []):
            if edge.source_session in nodes and edge.source_session not in ordered_ids:
                ordered_ids.append(edge.source_session)

    for session_id in nodes:
        if session_id not in ordered_ids:
            ordered_ids.append(session_id)

    return [nodes[session_id] for session_id in ordered_ids]


def build_recall_section(
    memory_dir: Path,
    query: str,
    domains: list[DomainRecord],
    seeds: list[SessionNode],
    nodes: list[SessionNode],
    edges: list[CausalEdge],
) -> str:
    if not nodes:
        return ""
    seed_ids = {node.session_id for node in seeds}
    incoming_contradictions: dict[str, list[str]] = {}
    for edge in edges:
        if edge.relation != "contradicts":
            continue
        incoming_contradictions.setdefault(edge.target_session, []).append(edge.source_session)

    lines = [
        "# Causal Session Memory",
        "",
        "Use this as cross-session project history. If details are needed, inspect the referenced transcript files.",
        "If causal memory conflicts with the current repo state, trust the current repo state.",
        "",
        f"Current query: {query}",
        "",
        "## Routed Domains",
    ]
    for domain in domains:
        desc = f" - {domain.description}" if domain.description else ""
        lines.append(f"- `{domain.slug}`{desc}")

    lines.extend(["", "## Session Chain"])
    for node in nodes[:8]:
        marker = "seed" if node.session_id in seed_ids else "linked"
        transcript_path = resolve_transcript_path(memory_dir, node)
        transcript_text = str(transcript_path) if transcript_path is not None else "(missing transcript)"
        lines.append(f"- `{node.session_id}` [{node.domain}] {node.title} ({node.outcome}, {marker})")
        if node.goal:
            lines.append(f"  Goal: {node.goal}")
        if node.key_decisions:
            top = node.key_decisions[0]
            reason = f" because {top.reason}" if top.reason else ""
            lines.append(f"  Decision: {top.decision}{reason}")
        if node.key_findings:
            lines.append(f"  Finding: {node.key_findings[0]}")
        if node.open_questions:
            lines.append(f"  Open: {node.open_questions[0]}")
        if node.session_id in incoming_contradictions:
            lines.append(f"  Superseded by: {', '.join(incoming_contradictions[node.session_id][:3])}")
        lines.append(f"  Transcript: {transcript_text} ({node.transcript_ref.turns} lines)")

    return "\n".join(lines)


def touch_recalled_sessions(memory_dir: Path, result: RecallResult) -> None:
    if result.session_ids:
        mark_sessions_accessed(memory_dir, result.session_ids)


def _terms(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9_./-]{2,}", (text or "").lower())
    stop = {"to", "the", "a", "an", "of", "in", "on", "for", "and", "or", "is", "it", "we"}
    out: list[str] = []
    seen: set[str] = set()
    for word in words:
        if word in stop:
            continue
        if word not in seen:
            seen.add(word)
            out.append(word)
    return out
