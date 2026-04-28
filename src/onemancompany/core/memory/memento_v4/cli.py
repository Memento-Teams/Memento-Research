"""Memento v4 CLI — wrappers for shell hooks and dry-run validation.

Subcommands:
  ingest   — feed a sessions-dir into the causal graph (writes disk)
  recall   — rebuild in-memory state from sessions-dir, then query

Sessions-dir layout (one JSON per session, sorted by name):
  sessions/
    01.json
    02.json

Each file:
  {
    "session_num": 1,
    "date_time": "2026-04-01T10:00:00Z",
    "turns": [
      {"speaker": "user", "text": "..."},
      {"speaker": "assistant", "text": "..."}
    ]
  }
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .ablation import AblationFlags
from .adapter import MemoryV4Adapter, RecallContext
from .types import Conversation, Session, Turn


def _load_sessions(sessions_dir: Path) -> list[Session]:
    sessions: list[Session] = []
    for path in sorted(sessions_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
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
    return sessions


def _load_conversation(sessions_dir: Path, conv_id_int: int) -> Conversation:
    sessions = _load_sessions(sessions_dir)
    return Conversation(conv_id=conv_id_int, sessions=sessions)


def _ablation_from_args(args: argparse.Namespace) -> AblationFlags:
    return AblationFlags(
        classify_memories=not args.no_classify,
        conflict_detection=not args.no_conflict,
        reflect_synthesis=args.reflect,
        bm25_scoring=not args.no_bm25,
        memory_summary=not args.no_memory_summary,
    )


def _build_adapter(args: argparse.Namespace, top_k: int = 20) -> MemoryV4Adapter:
    return MemoryV4Adapter(
        memory_root=Path(args.root),
        model=args.model,
        top_k=top_k,
        ablation=_ablation_from_args(args),
    )


async def _ingest(adapter: MemoryV4Adapter, conv: Conversation, conv_id: str) -> None:
    await adapter.setup()
    await adapter.ingest(conv, conv_id=conv_id)


async def _recall(
    adapter: MemoryV4Adapter, conv: Conversation, conv_id: str, query: str
) -> RecallContext:
    await adapter.setup()
    await adapter.ingest(conv, conv_id=conv_id)
    return await adapter.recall(query, conv_id=conv_id)


def cmd_ingest(args: argparse.Namespace) -> int:
    sessions_dir = Path(args.sessions_dir)
    if not sessions_dir.exists():
        print(f"sessions dir not found: {sessions_dir}", file=sys.stderr)
        return 2
    conv = _load_conversation(sessions_dir, conv_id_int=int(args.conv_id_int))
    if not conv.sessions:
        print("no sessions found in dir", file=sys.stderr)
        return 2
    adapter = _build_adapter(args)
    asyncio.run(_ingest(adapter, conv, args.conv_id))
    print(f"ingested {len(conv.sessions)} session(s) into {args.root}/conv_{args.conv_id}")
    return 0


def cmd_recall(args: argparse.Namespace) -> int:
    sessions_dir = Path(args.sessions_dir)
    if not sessions_dir.exists():
        print(f"sessions dir not found: {sessions_dir}", file=sys.stderr)
        return 2
    conv = _load_conversation(sessions_dir, conv_id_int=int(args.conv_id_int))
    if not conv.sessions:
        print("no sessions found in dir", file=sys.stderr)
        return 2
    adapter = _build_adapter(args, top_k=args.top_k)
    ctx = asyncio.run(_recall(adapter, conv, args.conv_id, args.query))
    if args.format == "json":
        out = {
            "raw_text": ctx.raw_text,
            "session_ids": ctx.session_ids,
            "metadata": ctx.metadata,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(ctx.raw_text or "(no recall context)")
    return 0


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", required=True, help="memory root directory")
    parser.add_argument("--conv-id", required=True, help="conversation id (e.g. employee id)")
    parser.add_argument("--conv-id-int", default="0", help="optional int id used internally")
    parser.add_argument("--model", default="gemini-3-flash-preview")
    parser.add_argument("--no-classify", action="store_true")
    parser.add_argument("--no-conflict", action="store_true")
    parser.add_argument("--reflect", action="store_true",
                        help="enable reflect synthesis (extra LLM call per recall)")
    parser.add_argument("--no-bm25", action="store_true")
    parser.add_argument("--no-memory-summary", action="store_true")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="memento-cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ingest = sub.add_parser("ingest", help="ingest sessions into memory store")
    _add_common(p_ingest)
    p_ingest.add_argument("--sessions-dir", required=True)
    p_ingest.set_defaults(func=cmd_ingest)

    p_recall = sub.add_parser("recall", help="recall context for a query")
    _add_common(p_recall)
    p_recall.add_argument("--sessions-dir", required=True,
                          help="needed to rebuild in-memory state (vectors + bm25)")
    p_recall.add_argument("--query", required=True)
    p_recall.add_argument("--top-k", type=int, default=5)
    p_recall.add_argument("--format", choices=["text", "json"], default="text")
    p_recall.set_defaults(func=cmd_recall)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
