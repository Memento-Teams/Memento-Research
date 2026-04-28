"""Shared text utilities: tokenization, stop-word lists, boost detectors.

These helpers power the v4 hybrid scoring pipeline. They are tuned for the
LoCoMo/LongMemEval benchmark style where queries contain proper nouns,
quoted phrases, and temporal references.
"""
from __future__ import annotations

import re


STOP_WORDS = {
    "what", "when", "where", "who", "how", "which", "did", "do", "was",
    "were", "have", "has", "had", "is", "are", "the", "a", "an", "my",
    "me", "i", "you", "your", "their", "it", "its", "in", "on", "at",
    "to", "for", "of", "with", "by", "from", "ago", "last", "that",
    "this", "there", "about", "get", "got", "give", "gave", "buy",
    "bought", "made", "make", "said",
}

# Words that LOOK like proper nouns (start with capital) but aren't — used
# to filter false positives when extracting person/place names from queries.
NOT_NAMES = {
    "What", "When", "Where", "Who", "How", "Which", "Did", "Do", "Was",
    "Were", "Have", "Has", "Had", "Is", "Are", "The", "My", "Our",
    "Their", "Can", "Could", "Would", "Should", "Will", "Shall", "May",
    "Might", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
    "Saturday", "Sunday", "January", "February", "March", "April",
    "June", "July", "August", "September", "October", "November",
    "December", "In", "On", "At", "For", "To", "Of", "With", "By",
    "From", "And", "But", "I", "It", "Its", "This", "That", "These",
    "Those", "Previously", "Recently", "Also", "Just", "Very", "More",
    "Said", "Speaker", "Person", "Time", "Date", "Year", "Day",
}


def tokenize(text: str) -> list[str]:
    """Lower-case tokens (3+ chars), with stop words removed."""
    words = re.findall(r"\b[a-z]{3,}\b", text.lower())
    return [w for w in words if w not in STOP_WORDS]


def keyword_overlap(query_kws: list[str], doc_text: str) -> float:
    """Fallback: fraction of query keywords appearing in doc_text (for when
    BM25 scoring is ablated off)."""
    if not query_kws:
        return 0.0
    doc_lower = doc_text.lower()
    return sum(1 for kw in query_kws if kw in doc_lower) / len(query_kws)


def quoted_phrases(text: str) -> list[str]:
    """Extract quoted substrings (3-60 chars) from *text*.

    These are strong exact-match signals — when a user query contains a
    quoted phrase, it's almost certainly present verbatim in some session.
    """
    phrases: list[str] = []
    for pat in [r"'([^']{3,60})'", r'"([^"]{3,60})"']:
        phrases.extend(re.findall(pat, text))
    return [p.strip() for p in phrases if len(p.strip()) >= 3]


def quoted_boost(phrases: list[str], doc_text: str) -> float:
    """Fraction of quoted phrases that appear (case-insensitively) in doc_text."""
    if not phrases:
        return 0.0
    doc_lower = doc_text.lower()
    return min(
        sum(1 for p in phrases if p.lower() in doc_lower) / len(phrases),
        1.0,
    )


def person_names(text: str) -> list[str]:
    """Heuristic proper-noun extraction — capitalized tokens not in NOT_NAMES."""
    words = re.findall(r"\b[A-Z][a-z]{2,15}\b", text)
    return list({w for w in words if w not in NOT_NAMES})


def name_boost(names: list[str], doc_text: str) -> float:
    if not names:
        return 0.0
    doc_lower = doc_text.lower()
    return min(
        sum(1 for n in names if n.lower() in doc_lower) / len(names),
        1.0,
    )
