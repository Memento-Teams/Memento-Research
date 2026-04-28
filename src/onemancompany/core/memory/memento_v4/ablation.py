"""Feature-toggle flags for v4 ablation experiments.

Each flag independently enables/disables one of the 5 v4 optimizations.
With all 5 flags off the adapter behaves like v3 (vector + causal chain only).

Usage in the benchmark harness:

    from memento_v4 import AblationFlags, MemoryV4Adapter

    # All features on (default)
    adapter = MemoryV4Adapter(memory_root=..., ablation=AblationFlags())

    # Disable just Reflect (e.g. to measure its contribution)
    adapter = MemoryV4Adapter(
        memory_root=...,
        ablation=AblationFlags(reflect_synthesis=False),
    )

    # Full ablation — equivalent to v3 baseline
    adapter = MemoryV4Adapter(
        memory_root=...,
        ablation=AblationFlags.all_off(),
    )
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class AblationFlags:
    """Independent toggles for each v4 optimization."""

    # Opt 1: classify each finding as fact/preference/lesson + confidence
    #        (inspired by Hindsight's four-network separation)
    classify_memories: bool = True

    # Opt 2: detect cross-session fact conflicts and mark superseded sessions
    #        (inspired by EverOS lifecycle + Zep temporal validity)
    conflict_detection: bool = True

    # Opt 3: LLM-synthesize cross-session answer at recall time
    #        (inspired by Hindsight's reflect operation)
    reflect_synthesis: bool = True

    # Opt 4: BM25 lexical matching blended with vector distance
    #        (standard hybrid retrieval, tuned for exact-term recall)
    bm25_scoring: bool = True

    # Opt 5: always prepend domain-level MEMORY.md hot summary
    #        (inspired by MemPalace's 170-token cold-start strategy)
    memory_summary: bool = True

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)

    def enabled_list(self) -> list[str]:
        return [k for k, v in self.to_dict().items() if v]

    @classmethod
    def all_off(cls) -> "AblationFlags":
        """v3-equivalent baseline (all v4 features disabled)."""
        return cls(
            classify_memories=False,
            conflict_detection=False,
            reflect_synthesis=False,
            bm25_scoring=False,
            memory_summary=False,
        )
