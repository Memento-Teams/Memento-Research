"""Lightweight BM25 scorer (Okapi BM25, pure-Python, zero deps).

Used in the v4 hybrid recall pipeline to complement ChromaDB vector search
with precise lexical matching — critical for proper nouns, dates, and
specific terminology where vector similarity alone is noisy.

Typical use:

    scorer = BM25Scorer()
    for text in session_texts:
        scorer.add_document(tokenize(text))
    query_tokens = tokenize(query)
    scores = scorer.score_all(query_tokens)  # [doc0_score, doc1_score, ...]
"""
from __future__ import annotations

import math
from collections import Counter


class BM25Scorer:
    """Okapi BM25 operating on pre-tokenized documents.

    Parameters:
        k1: term-frequency saturation (1.2 is standard)
        b:  length normalization (0.75 is standard)
    """

    def __init__(self, k1: float = 1.2, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self._doc_freqs: Counter[str] = Counter()
        self._doc_lens: list[int] = []
        self._doc_tokens: list[list[str]] = []
        self._n_docs: int = 0
        self._avgdl: float = 0.0

    # ── build ───────────────────────────────────────────────────────────────

    def add_document(self, tokens: list[str]) -> None:
        self._doc_tokens.append(tokens)
        self._doc_lens.append(len(tokens))
        seen: set[str] = set()
        for t in tokens:
            if t not in seen:
                self._doc_freqs[t] += 1
                seen.add(t)
        self._n_docs += 1
        self._avgdl = sum(self._doc_lens) / max(self._n_docs, 1)

    # ── query ───────────────────────────────────────────────────────────────

    def score(self, query_tokens: list[str], doc_idx: int) -> float:
        """BM25 score for *query_tokens* against document at *doc_idx*."""
        if doc_idx < 0 or doc_idx >= self._n_docs:
            return 0.0
        doc_tokens = self._doc_tokens[doc_idx]
        dl = self._doc_lens[doc_idx]
        tf_map: Counter[str] = Counter(doc_tokens)
        score = 0.0
        for qt in query_tokens:
            n_qi = self._doc_freqs.get(qt, 0)
            if n_qi == 0:
                continue
            idf = math.log((self._n_docs - n_qi + 0.5) / (n_qi + 0.5) + 1.0)
            freq = tf_map.get(qt, 0)
            numerator = freq * (self.k1 + 1.0)
            denominator = freq + self.k1 * (
                1.0 - self.b + self.b * dl / max(self._avgdl, 1.0)
            )
            score += idf * numerator / denominator
        return score

    def score_all(self, query_tokens: list[str]) -> list[float]:
        return [self.score(query_tokens, i) for i in range(self._n_docs)]
