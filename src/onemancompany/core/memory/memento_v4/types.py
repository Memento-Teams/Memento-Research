"""Minimal data types that the v4 adapter accepts as input.

The adapter takes a `Conversation` (a sequence of `Session`s). Benchmark
harnesses are responsible for converting their native format into these
shapes before calling `ingest`.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Turn:
    """One utterance within a session."""
    speaker: str
    text: str
    blip_caption: str = ""  # optional image caption (LoCoMo)


@dataclass
class Session:
    """A contiguous conversation segment."""
    session_num: int
    turns: list[Turn] = field(default_factory=list)
    date_time: str = ""  # ISO-ish timestamp if available


@dataclass
class Conversation:
    """The full haystack for one benchmark question / dialogue."""
    conv_id: int
    sessions: list[Session] = field(default_factory=list)
    speaker_a: str = "user"
    speaker_b: str = "assistant"
