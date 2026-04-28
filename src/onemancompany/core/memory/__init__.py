"""OMC memory subsystem.

The memento_v4 subpackage is vendored from upstream (MIT). See LICENSE in
this directory for license terms. Public API re-exports keep call sites
short — `from onemancompany.core.memory import MemoryV4Adapter, AblationFlags`.
"""
from onemancompany.core.memory.memento_v4 import (
    AblationFlags,
    MemoryAdapter,
    MemoryV4Adapter,
    RecallContext,
)
from onemancompany.core.memory.memento_v4.types import (
    Conversation,
    Session,
    Turn,
)

__all__ = [
    "AblationFlags",
    "Conversation",
    "MemoryAdapter",
    "MemoryV4Adapter",
    "RecallContext",
    "Session",
    "Turn",
]
