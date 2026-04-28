from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import CausalEdge, DomainRecord, SessionNode

CAUSAL_DIR_NAME = "causal"
_DOMAIN_INDEX_JSON = "DOMAIN_INDEX.json"
_DOMAIN_INDEX_MD = "DOMAIN_INDEX.md"
_CROSS_DOMAIN_EDGES = "cross_domain_edges.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def causal_root(memory_dir: Path) -> Path:
    return memory_dir / CAUSAL_DIR_NAME


def _meta_dir(memory_dir: Path) -> Path:
    return causal_root(memory_dir) / "_meta"


def _global_dir(memory_dir: Path) -> Path:
    return causal_root(memory_dir) / "_global"


def domain_slug(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (name or "").strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "general"


def ensure_causal_memory_dir(memory_dir: Path) -> None:
    root = causal_root(memory_dir)
    root.mkdir(parents=True, exist_ok=True)
    _meta_dir(memory_dir).mkdir(parents=True, exist_ok=True)
    _global_dir(memory_dir).mkdir(parents=True, exist_ok=True)

    domain_index_json = root / _DOMAIN_INDEX_JSON
    if not domain_index_json.exists():
        _json_write(domain_index_json, {"domains": []})

    cross_domain_edges = _meta_dir(memory_dir) / _CROSS_DOMAIN_EDGES
    if not cross_domain_edges.exists():
        _json_write(cross_domain_edges, [])

    global_memory = _global_dir(memory_dir) / "MEMORY.md"
    if not global_memory.exists():
        global_memory.write_text(
            "# Global Causal Memory\n\n"
            "Long-lived user preferences still live in the main MEMORY.md.\n",
            encoding="utf-8",
        )

    domain_index_md = root / _DOMAIN_INDEX_MD
    if not domain_index_md.exists():
        _write_domain_index_md(memory_dir, [])


def _json_read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _json_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_domain_index(memory_dir: Path) -> list[DomainRecord]:
    ensure_causal_memory_dir(memory_dir)
    raw = _json_read(causal_root(memory_dir) / _DOMAIN_INDEX_JSON, {"domains": []})
    domains = raw.get("domains", []) if isinstance(raw, dict) else []
    out = [
        DomainRecord.from_dict(item)
        for item in domains
        if isinstance(item, dict) and item.get("slug")
    ]
    out.sort(key=lambda item: item.updated_at, reverse=True)
    return out


def save_domain_index(memory_dir: Path, domains: list[DomainRecord]) -> None:
    ensure_causal_memory_dir(memory_dir)
    deduped: dict[str, DomainRecord] = {}
    for domain in domains:
        if not domain.slug:
            continue
        deduped[domain.slug] = domain
    ordered = sorted(deduped.values(), key=lambda item: item.updated_at, reverse=True)
    _json_write(
        causal_root(memory_dir) / _DOMAIN_INDEX_JSON,
        {"domains": [item.to_dict() for item in ordered]},
    )
    _write_domain_index_md(memory_dir, ordered)


def _write_domain_index_md(memory_dir: Path, domains: list[DomainRecord]) -> None:
    path = causal_root(memory_dir) / _DOMAIN_INDEX_MD
    lines = [
        "# Causal Domain Index",
        "",
        "This index summarizes domain-partitioned session memory.",
        "",
    ]
    if not domains:
        lines.append("- No domains yet.")
    else:
        for domain in domains:
            desc = domain.description or "No description yet."
            lines.append(
                f"- `{domain.slug}` ({domain.session_count} sessions) - {desc}"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def domain_dir(memory_dir: Path, domain_name: str) -> Path:
    slug = domain_slug(domain_name)
    return causal_root(memory_dir) / slug


def domain_sessions_dir(memory_dir: Path, domain_name: str) -> Path:
    path = domain_dir(memory_dir, domain_name) / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def domain_edges_path(memory_dir: Path, domain_name: str) -> Path:
    path = domain_dir(memory_dir, domain_name) / "edges.json"
    if not path.exists():
        _json_write(path, [])
    return path


def domain_memory_path(memory_dir: Path, domain_name: str) -> Path:
    path = domain_dir(memory_dir, domain_name) / "MEMORY.md"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Domain Session Memory\n", encoding="utf-8")
    return path


def load_domain_sessions(memory_dir: Path, domain_name: str) -> list[SessionNode]:
    sessions_dir = domain_sessions_dir(memory_dir, domain_name)
    out: list[SessionNode] = []
    for path in sessions_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            out.append(SessionNode.from_dict(data))
    out.sort(key=lambda item: item.updated_at or item.created_at, reverse=True)
    return out


def load_all_sessions(memory_dir: Path) -> list[SessionNode]:
    out: list[SessionNode] = []
    for domain in load_domain_index(memory_dir):
        out.extend(load_domain_sessions(memory_dir, domain.slug))
    out.sort(key=lambda item: item.updated_at or item.created_at, reverse=True)
    return out


def list_recent_sessions(
    memory_dir: Path,
    limit: int = 20,
    exclude_session_ids: set[str] | None = None,
) -> list[SessionNode]:
    exclude_session_ids = exclude_session_ids or set()
    out = [
        node for node in load_all_sessions(memory_dir)
        if node.session_id not in exclude_session_ids
    ]
    return out[:limit]


def find_session_node(memory_dir: Path, session_id: str) -> SessionNode | None:
    for node in load_all_sessions(memory_dir):
        if node.session_id == session_id:
            return node
    return None


def find_session_by_prefix(memory_dir: Path, prefix: str) -> SessionNode | None:
    prefix = prefix.lower().strip()
    if not prefix:
        return None
    for node in load_all_sessions(memory_dir):
        if node.session_id.lower().startswith(prefix):
            return node
    return None


def save_session_node(
    memory_dir: Path,
    node: SessionNode,
    transcript_source_path: Path | None,
    domain_description: str = "",
) -> SessionNode:
    ensure_causal_memory_dir(memory_dir)
    now = _now_iso()
    node.domain = domain_slug(node.domain)
    node.updated_at = now
    if not node.created_at:
        node.created_at = now
    if not node.last_accessed:
        node.last_accessed = now

    previous = find_session_node(memory_dir, node.session_id)
    previous_domain = previous.domain if previous is not None else ""
    if previous_domain and previous_domain != node.domain:
        _remove_session_artifacts(memory_dir, previous_domain, node.session_id)

    sessions_dir = domain_sessions_dir(memory_dir, node.domain)
    transcript_dir = sessions_dir / node.session_id
    transcript_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_dir / "transcript.jsonl"
    if transcript_source_path is not None and transcript_source_path.exists():
        shutil.copyfile(transcript_source_path, transcript_path)
        node.transcript_ref.file = str(transcript_path.relative_to(causal_root(memory_dir)))

    node_path = sessions_dir / f"{node.session_id}.json"
    _json_write(node_path, node.to_dict())

    _update_domain_record(memory_dir, node, domain_description)
    _render_domain_memory(memory_dir, node.domain)
    return node


def _remove_session_artifacts(memory_dir: Path, domain_name: str, session_id: str) -> None:
    sessions_dir = domain_sessions_dir(memory_dir, domain_name)
    node_path = sessions_dir / f"{session_id}.json"
    transcript_dir = sessions_dir / session_id
    try:
        node_path.unlink(missing_ok=True)
    except OSError:
        pass
    shutil.rmtree(transcript_dir, ignore_errors=True)
    _render_domain_memory(memory_dir, domain_name)
    _recount_domain(memory_dir, domain_name)


def _update_domain_record(memory_dir: Path, node: SessionNode, domain_description: str) -> None:
    domains = load_domain_index(memory_dir)
    by_slug = {item.slug: item for item in domains}
    record = by_slug.get(node.domain)
    if record is None:
        record = DomainRecord(name=node.domain, slug=node.domain)
    if domain_description and not record.description:
        record.description = domain_description
    if node.cwd and node.cwd not in record.cwd_hints:
        record.cwd_hints = [node.cwd] + [item for item in record.cwd_hints if item != node.cwd]
    for keyword in node.keywords[:12]:
        if keyword not in record.keywords:
            record.keywords.append(keyword)
    record.keywords = record.keywords[:24]
    record.updated_at = node.updated_at
    if node.session_id in record.recent_session_ids:
        record.recent_session_ids.remove(node.session_id)
    record.recent_session_ids.insert(0, node.session_id)
    record.recent_session_ids = record.recent_session_ids[:10]
    record.session_count = len(load_domain_sessions(memory_dir, node.domain))
    by_slug[record.slug] = record
    save_domain_index(memory_dir, list(by_slug.values()))


def _recount_domain(memory_dir: Path, domain_name: str) -> None:
    domains = load_domain_index(memory_dir)
    changed = False
    for domain in domains:
        if domain.slug != domain_slug(domain_name):
            continue
        domain.session_count = len(load_domain_sessions(memory_dir, domain.slug))
        changed = True
        break
    if changed:
        save_domain_index(memory_dir, domains)


def _render_domain_memory(memory_dir: Path, domain_name: str) -> None:
    sessions = load_domain_sessions(memory_dir, domain_name)
    path = domain_memory_path(memory_dir, domain_name)
    lines = [
        f"# Domain Memory: {domain_slug(domain_name)}",
        "",
        "Recent structured session summaries in this domain.",
        "",
    ]
    if not sessions:
        lines.append("- No sessions yet.")
    else:
        for node in sessions[:50]:
            open_q = f" Open: {node.open_questions[0]}" if node.open_questions else ""
            keywords = f" Keywords: {', '.join(node.keywords[:4])}" if node.keywords else ""
            lines.append(
                f"- `{node.session_id}` {node.title} [{node.outcome}]."
                f"{open_q}{keywords}"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_edges(memory_dir: Path, domain_name: str) -> list[CausalEdge]:
    raw = _json_read(domain_edges_path(memory_dir, domain_name), [])
    return [
        CausalEdge.from_dict(item)
        for item in raw
        if isinstance(item, dict)
    ]


def load_cross_domain_edges(memory_dir: Path) -> list[CausalEdge]:
    raw = _json_read(_meta_dir(memory_dir) / _CROSS_DOMAIN_EDGES, [])
    return [
        CausalEdge.from_dict(item)
        for item in raw
        if isinstance(item, dict)
    ]


def load_all_edges(memory_dir: Path) -> list[CausalEdge]:
    out: list[CausalEdge] = []
    for domain in load_domain_index(memory_dir):
        out.extend(load_edges(memory_dir, domain.slug))
    out.extend(load_cross_domain_edges(memory_dir))
    return out


def upsert_edges(memory_dir: Path, edges: list[CausalEdge]) -> None:
    ensure_causal_memory_dir(memory_dir)
    domain_groups: dict[str, list[CausalEdge]] = {}
    cross_domain: list[CausalEdge] = []

    for edge in edges:
        if not edge.source_session or not edge.target_session or not edge.relation:
            continue
        if edge.source_domain and edge.target_domain and edge.source_domain == edge.target_domain:
            domain_groups.setdefault(edge.source_domain, []).append(edge)
        else:
            cross_domain.append(edge)

    for domain_name, group in domain_groups.items():
        existing = load_edges(memory_dir, domain_name)
        merged = _merge_edges(existing, group)
        _json_write(domain_edges_path(memory_dir, domain_name), [item.to_dict() for item in merged])

    if cross_domain:
        existing = load_cross_domain_edges(memory_dir)
        merged = _merge_edges(existing, cross_domain)
        _json_write(_meta_dir(memory_dir) / _CROSS_DOMAIN_EDGES, [item.to_dict() for item in merged])


def _merge_edges(existing: list[CausalEdge], incoming: list[CausalEdge]) -> list[CausalEdge]:
    merged: dict[tuple[str, str, str], CausalEdge] = {
        (edge.source_session, edge.target_session, edge.relation): edge
        for edge in existing
    }
    for edge in incoming:
        key = (edge.source_session, edge.target_session, edge.relation)
        merged[key] = edge
    out = list(merged.values())
    out.sort(key=lambda edge: edge.created_at, reverse=True)
    return out


def update_session_links(memory_dir: Path, session_id: str, predecessor_ids: list[str]) -> None:
    node = find_session_node(memory_dir, session_id)
    if node is None:
        return
    predecessor_ids = [item for item in predecessor_ids if item and item != session_id]
    node.continues_from = predecessor_ids
    save_session_node(memory_dir, node, transcript_source_path=None)

    for predecessor_id in predecessor_ids:
        predecessor = find_session_node(memory_dir, predecessor_id)
        if predecessor is None:
            continue
        if session_id not in predecessor.continued_by:
            predecessor.continued_by.append(session_id)
            save_session_node(memory_dir, predecessor, transcript_source_path=None)


def resolve_transcript_path(memory_dir: Path, node: SessionNode) -> Path | None:
    if not node.transcript_ref.file:
        return None
    return causal_root(memory_dir) / node.transcript_ref.file


def mark_sessions_accessed(memory_dir: Path, session_ids: list[str]) -> None:
    seen: set[str] = set()
    for session_id in session_ids:
        if not session_id or session_id in seen:
            continue
        seen.add(session_id)
        node = find_session_node(memory_dir, session_id)
        if node is None:
            continue
        node.access_count += 1
        node.last_accessed = _now_iso()
        save_session_node(memory_dir, node, transcript_source_path=None)


def count_jsonl_lines(path: Path) -> int:
    try:
        with path.open(encoding="utf-8") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def needs_finalization(memory_dir: Path, session_id: str, raw_transcript_path: Path) -> bool:
    if not raw_transcript_path.exists():
        return False
    turns = count_jsonl_lines(raw_transcript_path)
    if turns == 0:
        return False
    node = find_session_node(memory_dir, session_id)
    if node is None:
        return True
    return node.transcript_ref.turns != turns
