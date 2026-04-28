"""Memento v4 MCP server — exposes recall/finalize/search/add_fact tools.

Lifecycle: a single server process holds the in-memory MemoryV4Adapter state
for one (memory_root, conv_id) pair. Sessions live on disk under
``$MEMENTO_SESSIONS_DIR``; the server re-ingests them on first recall and
again whenever ``memory_finalize_session`` is called (the adapter skips
already-finalized sessions, so re-LLM cost stays at the new session only).

Env vars:
  MEMENTO_ROOT          — memory root directory
  MEMENTO_CONV_ID       — conversation id (e.g. employee id)
  MEMENTO_SESSIONS_DIR  — dir with NN.json session files
  MEMENTO_MODEL         — LLM model (default gemini-3-flash-preview)
  OPENROUTER_API_KEY    — LLM credential (read by adapter)
  OPENROUTER_BASE_URL   — LLM endpoint
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from threading import Lock

from .ablation import AblationFlags
from .adapter import MemoryV4Adapter
from .types import Conversation, Session, Turn


_DEFAULT_MODEL = "gemini-3-flash-preview"


class _Server:
    """Holds adapter state for one (root, conv_id) pair."""

    def __init__(self) -> None:
        self.root = Path(os.environ.get("MEMENTO_ROOT", "./memento_memory"))
        self.conv_id = os.environ.get("MEMENTO_CONV_ID", "default")
        self.sessions_dir = Path(
            os.environ.get("MEMENTO_SESSIONS_DIR", str(self.root / "sessions"))
        )
        self.model = os.environ.get("MEMENTO_MODEL", _DEFAULT_MODEL)
        self.adapter: MemoryV4Adapter | None = None
        self.ingested = False
        self._lock = Lock()

    def _ensure_dirs(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _build_adapter(self) -> MemoryV4Adapter:
        return MemoryV4Adapter(
            memory_root=self.root,
            model=self.model,
            # phase-1 defaults: classify + conflict + bm25 + memory_summary on, reflect off
            ablation=AblationFlags(reflect_synthesis=False),
        )

    def _load_conversation(self) -> Conversation:
        sessions: list[Session] = []
        for path in sorted(self.sessions_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            turns = [
                Turn(speaker=t.get("speaker", "user"), text=t.get("text", ""))
                for t in data.get("turns", [])
            ]
            sessions.append(
                Session(
                    session_num=int(data["session_num"]),
                    turns=turns,
                    date_time=data.get("date_time", ""),
                )
            )
        return Conversation(conv_id=0, sessions=sessions)

    async def _ensure_ingested(self) -> None:
        with self._lock:
            if self.ingested and self.adapter is not None:
                return
            self._ensure_dirs()
            self.adapter = self._build_adapter()
            await self.adapter.setup()
            conv = self._load_conversation()
            if conv.sessions:
                await self.adapter.ingest(conv, conv_id=self.conv_id)
            self.ingested = True

    async def _reingest(self) -> None:
        with self._lock:
            self.adapter = self._build_adapter()
            await self.adapter.setup()
            conv = self._load_conversation()
            if conv.sessions:
                await self.adapter.ingest(conv, conv_id=self.conv_id)
            self.ingested = True

    # ── Tool handlers ────────────────────────────────────────────────────

    async def recall(self, query: str, top_k: int = 5) -> str:
        await self._ensure_ingested()
        assert self.adapter is not None
        if top_k != self.adapter.top_k:
            self.adapter.top_k = top_k
        ctx = await self.adapter.recall(query, conv_id=self.conv_id)
        return ctx.raw_text or "(no recall context)"

    async def finalize_session(
        self, session_num: int, transcript: list[dict] | None = None,
        date_time: str = "",
    ) -> str:
        """Write a session file (if `transcript` given) then re-ingest."""
        self._ensure_dirs()
        if transcript is not None:
            session_file = self.sessions_dir / f"{session_num:03d}.json"
            payload = {
                "session_num": session_num,
                "date_time": date_time,
                "turns": transcript,
            }
            session_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        await self._reingest()
        return f"finalized session {session_num} (total sessions: {len(list(self.sessions_dir.glob('*.json')))})"

    def search_md(self, pattern: str) -> str:
        memory_md = self.root / f"conv_{self.conv_id}" / "MEMORY.md"
        if not memory_md.exists():
            return "(MEMORY.md not yet written)"
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            regex = re.compile(re.escape(pattern), re.IGNORECASE)
        hits: list[str] = []
        for line in memory_md.read_text(encoding="utf-8").splitlines():
            if regex.search(line):
                hits.append(line)
        return "\n".join(hits) if hits else "(no matches)"

    def add_fact(self, text: str, fact_type: str = "fact") -> str:
        """Append a fact to the working buffer for later distillation."""
        buffer_path = self.root / f"conv_{self.conv_id}" / "working_buffer.jsonl"
        buffer_path.parent.mkdir(parents=True, exist_ok=True)
        from datetime import datetime, timezone
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": fact_type,
            "text": text,
        }
        with buffer_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return f"buffered {fact_type}: {text[:60]}"


_STATE = _Server()


def main(argv: list[str] | None = None) -> int:
    """Entry point for `memento-mcp` console script."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        print(
            "memento-mcp requires the 'mcp' package. Install with:\n"
            "  pip install memento_v4[mcp]",
            file=sys.stderr,
        )
        return 2

    server = FastMCP("memento")

    @server.tool()
    async def memory_recall(query: str, top_k: int = 5) -> str:
        """Retrieve relevant past sessions for a query.

        Returns a markdown context block including raw snippets, causal
        edges, and (optionally) a synthesized answer.
        """
        return await _STATE.recall(query, top_k=top_k)

    @server.tool()
    async def memory_finalize_session(
        session_num: int,
        transcript: list[dict] | None = None,
        date_time: str = "",
    ) -> str:
        """Persist + finalize a session.

        Pass `transcript` as a list of {"speaker": ..., "text": ...} turns.
        If omitted, the server re-ingests whatever session files already
        exist on disk (useful when the hook script wrote the file directly).
        """
        return await _STATE.finalize_session(
            session_num=session_num, transcript=transcript, date_time=date_time,
        )

    @server.tool()
    def memory_search_md(pattern: str) -> str:
        """Grep MEMORY.md for a regex/substring pattern."""
        return _STATE.search_md(pattern)

    @server.tool()
    def memory_add_fact(text: str, fact_type: str = "fact") -> str:
        """Append a fact/preference/decision to the working buffer.

        fact_type is one of: fact, preference, decision, lesson.
        """
        return _STATE.add_fact(text, fact_type=fact_type)

    server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
