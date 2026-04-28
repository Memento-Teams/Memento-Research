from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    out: list[int] = []
    for item in value:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


@dataclass
class DecisionRecord:
    decision: str
    reason: str = ""
    alternatives: list[str] = field(default_factory=list)
    confidence: float = 0.0
    evidence_turns: list[int] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DecisionRecord":
        confidence = data.get("confidence", 0.0)
        try:
            confidence_val = float(confidence)
        except (TypeError, ValueError):
            confidence_val = 0.0
        confidence_val = min(max(confidence_val, 0.0), 1.0)
        return cls(
            decision=str(data.get("decision", "")).strip(),
            reason=str(data.get("reason", "")).strip(),
            alternatives=_string_list(data.get("alternatives")),
            confidence=confidence_val,
            evidence_turns=_int_list(data.get("evidence_turns")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ArtifactRef:
    description: str
    ref: str
    size_chars: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ArtifactRef":
        try:
            size_chars = int(data.get("size_chars", 0) or 0)
        except (TypeError, ValueError):
            size_chars = 0
        return cls(
            description=str(data.get("description", "")).strip(),
            ref=str(data.get("ref", "")).strip(),
            size_chars=max(size_chars, 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TranscriptRef:
    file: str
    turns: int = 0
    tokens_approx: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TranscriptRef":
        try:
            turns = int(data.get("turns", 0) or 0)
        except (TypeError, ValueError):
            turns = 0
        try:
            tokens_approx = int(data.get("tokens_approx", 0) or 0)
        except (TypeError, ValueError):
            tokens_approx = 0
        return cls(
            file=str(data.get("file", "")).strip(),
            turns=max(turns, 0),
            tokens_approx=max(tokens_approx, 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CausalEdge:
    source_session: str
    target_session: str
    relation: str
    evidence: str = ""
    source_domain: str = ""
    target_domain: str = ""
    created_at: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CausalEdge":
        return cls(
            source_session=str(data.get("source_session", "")).strip(),
            target_session=str(data.get("target_session", "")).strip(),
            relation=str(data.get("relation", "")).strip(),
            evidence=str(data.get("evidence", "")).strip(),
            source_domain=str(data.get("source_domain", "")).strip(),
            target_domain=str(data.get("target_domain", "")).strip(),
            created_at=str(data.get("created_at", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DomainRecord:
    name: str
    slug: str
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    cwd_hints: list[str] = field(default_factory=list)
    session_count: int = 0
    updated_at: str = ""
    recent_session_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DomainRecord":
        try:
            session_count = int(data.get("session_count", 0) or 0)
        except (TypeError, ValueError):
            session_count = 0
        return cls(
            name=str(data.get("name", "")).strip(),
            slug=str(data.get("slug", "")).strip(),
            description=str(data.get("description", "")).strip(),
            keywords=_string_list(data.get("keywords")),
            cwd_hints=_string_list(data.get("cwd_hints")),
            session_count=max(session_count, 0),
            updated_at=str(data.get("updated_at", "")).strip(),
            recent_session_ids=_string_list(data.get("recent_session_ids")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SessionNode:
    session_id: str
    domain: str
    title: str
    goal: str = ""
    outcome: str = "partial"
    key_decisions: list[DecisionRecord] = field(default_factory=list)
    key_findings: list[str] = field(default_factory=list)
    errors_lessons: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    files_touched: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    key_quotes: list[dict] = field(default_factory=list)  # [{"speaker": "", "turn": 0, "quote": ""}]
    transcript_ref: TranscriptRef = field(default_factory=lambda: TranscriptRef(file=""))
    artifacts: list[ArtifactRef] = field(default_factory=list)
    continues_from: list[str] = field(default_factory=list)
    continued_by: list[str] = field(default_factory=list)
    cwd: str = ""
    created_at: str = ""
    updated_at: str = ""
    last_accessed: str = ""
    access_count: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionNode":
        try:
            access_count = int(data.get("access_count", 0) or 0)
        except (TypeError, ValueError):
            access_count = 0
        return cls(
            session_id=str(data.get("session_id", "")).strip(),
            domain=str(data.get("domain", "")).strip(),
            title=str(data.get("title", "")).strip(),
            goal=str(data.get("goal", "")).strip(),
            outcome=str(data.get("outcome", "partial") or "partial").strip(),
            key_decisions=[
                DecisionRecord.from_dict(item)
                for item in data.get("key_decisions", [])
                if isinstance(item, dict)
            ],
            key_findings=_string_list(data.get("key_findings")),
            errors_lessons=_string_list(data.get("errors_lessons")),
            open_questions=_string_list(data.get("open_questions")),
            files_touched=_string_list(data.get("files_touched")),
            keywords=_string_list(data.get("keywords")),
            key_quotes=data.get("key_quotes", []) if isinstance(data.get("key_quotes"), list) else [],
            transcript_ref=TranscriptRef.from_dict(data.get("transcript_ref", {})),
            artifacts=[
                ArtifactRef.from_dict(item)
                for item in data.get("artifacts", [])
                if isinstance(item, dict)
            ],
            continues_from=_string_list(data.get("continues_from")),
            continued_by=_string_list(data.get("continued_by")),
            cwd=str(data.get("cwd", "")).strip(),
            created_at=str(data.get("created_at", "")).strip(),
            updated_at=str(data.get("updated_at", "")).strip(),
            last_accessed=str(data.get("last_accessed", "")).strip(),
            access_count=max(access_count, 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "domain": self.domain,
            "title": self.title,
            "goal": self.goal,
            "outcome": self.outcome,
            "key_decisions": [item.to_dict() for item in self.key_decisions],
            "key_findings": list(self.key_findings),
            "errors_lessons": list(self.errors_lessons),
            "open_questions": list(self.open_questions),
            "files_touched": list(self.files_touched),
            "keywords": list(self.keywords),
            "key_quotes": list(self.key_quotes),
            "transcript_ref": self.transcript_ref.to_dict(),
            "artifacts": [item.to_dict() for item in self.artifacts],
            "continues_from": list(self.continues_from),
            "continued_by": list(self.continued_by),
            "cwd": self.cwd,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_accessed": self.last_accessed,
            "access_count": self.access_count,
        }

    def search_blob(self) -> str:
        parts = [
            self.session_id,
            self.domain,
            self.title,
            self.goal,
            self.outcome,
            " ".join(self.key_findings),
            " ".join(self.errors_lessons),
            " ".join(self.open_questions),
            " ".join(self.files_touched),
            " ".join(self.keywords),
            " ".join(item.decision for item in self.key_decisions),
            " ".join(item.reason for item in self.key_decisions),
            " ".join(q.get("quote", "") for q in self.key_quotes if isinstance(q, dict)),
        ]
        return "\n".join(part for part in parts if part)

