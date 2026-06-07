"""#138: the critic verdict parser must accept Markdown-bold PASS/REJECT,
and the on-disk fallback must match the real ``gate_review_stage{N}.md``
filename the critic actually writes.

Repro: Stage 1 produced a valid topic refinement, the critic wrote
``gate_review_stage1.md`` containing ``### 2. Decision: **PASS**``, but the
engine (a) globbed the inverted ``stage1_gate_review*.md`` so the file was
never read, and (b) couldn't parse the bolded label — verdict came back
ambiguous → defaulted to REJECT → 6 retries → ``stage_1_retries_exhausted``.
"""
from __future__ import annotations

import pytest

from onemancompany.core.pipeline_engine import PipelineEngine


@pytest.mark.parametrize(
    "text,expected",
    [
        # the literal content of the failing run's gate_review_stage1.md
        ("### 2. Decision: **PASS**", True),
        # bolded LABEL — the case that broke the separator match
        ("- **Decision**: PASS", True),
        ("**Decision**: PASS", True),
        # bolded VALUE
        ("Decision: **PASS**", True),
        # plain / table / mixed
        ("Decision: PASS", True),
        ("| Decision | PASS |", True),
        ("| **Decision** | **PASS** |", True),
        ("**Confidence**: 0.84 | **Decision**: PASS", True),
        ("Verdict: PASS", True),
        # REJECT variants
        ("**Decision**: REJECT", False),
        ("- **Decision**: REJECT", False),
        ("| Decision | REJECT |", False),
        # ambiguous — must NOT be coerced to a verdict (#19 guard)
        ("Some prose with no labeled verdict.", None),
        ("Auto-REJECT trigger check passed.", None),
        ("", None),
    ],
)
def test_verdict_from_text_handles_markdown_bold(text, expected):
    assert PipelineEngine._verdict_from_text(text) is expected
