"""Memento v4 — causal-graph-based cross-session memory for LLM agents.

Public API:

    from memento_v4 import MemoryV4Adapter, AblationFlags
    from memento_v4.types import Conversation, Session, Turn

    adapter = MemoryV4Adapter(memory_root="./memory", ablation=AblationFlags())
    await adapter.setup()
    await adapter.ingest(conversation, conv_id="demo")
    ctx = await adapter.recall("What did we decide about the database?", "demo")
    print(ctx.raw_text)
"""
from .ablation import AblationFlags
from .adapter import MemoryAdapter, MemoryV4Adapter, RecallContext
from .bm25 import BM25Scorer
from .types import Conversation, Session, Turn

__version__ = "0.1.0"

__all__ = [
    "AblationFlags",
    "BM25Scorer",
    "Conversation",
    "MemoryAdapter",
    "MemoryV4Adapter",
    "RecallContext",
    "Session",
    "Turn",
]
