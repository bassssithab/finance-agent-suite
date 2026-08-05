"""Document/Chunk/SearchResult shapes shared across ingestion and retrieval.
See docs/ARCHITECTURE.md (`platform/knowledge`).
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Document:
    """One source document before chunking (a standard, policy, workpaper, ...).

    `metadata` carries fields like period/entity/framework for future filtered
    retrieval — filtering on them is deferred, see platform/knowledge/README.md.
    """

    doc_id: str
    title: str
    corpus: str
    text: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Chunk:
    """A retrieval-sized slice of a Document, still traceable back to it.

    Citations are mandatory in every generated output (docs/ARCHITECTURE.md),
    so every chunk carries enough of its source's identity to be cited.
    """

    chunk_id: str
    doc_id: str
    doc_title: str
    corpus: str
    position: int
    text: str
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def citation(self) -> str:
        return f"{self.doc_title} ({self.corpus}), chunk {self.position}"


@dataclass(frozen=True)
class SearchResult:
    chunk: Chunk
    score: float
