"""Retrieval entry point agents call through. See docs/ARCHITECTURE.md.

Ingestion pipeline is parse -> chunk -> index (the embed/vector step isn't
implemented yet; see README). Agents must retrieve through KnowledgeBase
rather than querying a store directly (CLAUDE.md golden rule #1).
"""

from .bm25 import BM25Index
from .chunking import chunk_document
from .models import Document, SearchResult


class KnowledgeBase:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self._index = BM25Index(k1=k1, b=b)
        self._documents: dict[str, Document] = {}

    def ingest(self, documents: list[Document]) -> None:
        """Add or replace documents (by doc_id) and rebuild the index over
        the full current document set."""
        for document in documents:
            self._documents[document.doc_id] = document

        chunks = []
        for document in self._documents.values():
            chunks.extend(chunk_document(document))
        self._index.build(chunks)

    def search(self, query: str, top_k: int = 5) -> list[SearchResult]:
        return self._index.search(query, top_k=top_k)
