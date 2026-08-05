"""Stdlib-only Okapi BM25 ranking over a fixed set of chunks.
See docs/ARCHITECTURE.md (`platform/knowledge`).

Hybrid retrieval (BM25 + vector) is the long-term design; this implements
the lexical half only, with no external dependencies. Vector search and
metadata filters (period, entity, framework) are deferred — see
platform/knowledge/README.md for the tracked gap.
"""

import math
import re
from collections import Counter

from .models import Chunk, SearchResult

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class BM25Index:
    """Okapi BM25 (k1, b as in the standard formulation), built once from a
    full chunk set and queried many times."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self._chunks: list[Chunk] = []
        self._term_freqs: list[Counter] = []
        self._doc_freq: Counter = Counter()
        self._chunk_lengths: list[int] = []
        self._avg_chunk_length: float = 0.0

    def build(self, chunks: list[Chunk]) -> None:
        self._chunks = list(chunks)
        self._term_freqs = []
        self._doc_freq = Counter()
        self._chunk_lengths = []

        for chunk in self._chunks:
            term_freq = Counter(tokenize(chunk.text))
            self._term_freqs.append(term_freq)
            self._chunk_lengths.append(sum(term_freq.values()))
            for term in term_freq:
                self._doc_freq[term] += 1

        self._avg_chunk_length = (
            sum(self._chunk_lengths) / len(self._chunk_lengths) if self._chunk_lengths else 0.0
        )

    def search(self, query: str, top_k: int = 5) -> list[SearchResult]:
        if not self._chunks:
            return []

        query_terms = tokenize(query)
        if not query_terms:
            return []

        n = len(self._chunks)
        scores = [0.0] * n

        for term in set(query_terms):
            df = self._doc_freq.get(term, 0)
            if df == 0:
                continue
            idf = math.log((n - df + 0.5) / (df + 0.5) + 1)
            for i, term_freq in enumerate(self._term_freqs):
                f = term_freq.get(term, 0)
                if f == 0:
                    continue
                length_norm = 1 - self.b + self.b * (self._chunk_lengths[i] / self._avg_chunk_length)
                scores[i] += idf * (f * (self.k1 + 1)) / (f + self.k1 * length_norm)

        ranked = [
            SearchResult(chunk=self._chunks[i], score=scores[i])
            for i in range(n) if scores[i] > 0
        ]
        ranked.sort(key=lambda r: r.score, reverse=True)
        return ranked[:top_k]
