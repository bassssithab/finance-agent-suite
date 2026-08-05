"""Splits a Document into retrieval-sized Chunks. See docs/ARCHITECTURE.md.

Paragraph-first: whole paragraphs are packed together up to `max_chars`, only
splitting mid-paragraph when a single paragraph exceeds the limit on its own.
"""

from .models import Chunk, Document

_DEFAULT_MAX_CHARS = 800


def chunk_document(document: Document, max_chars: int = _DEFAULT_MAX_CHARS) -> list[Chunk]:
    paragraphs = [p.strip() for p in document.text.split("\n\n") if p.strip()]

    chunks: list[Chunk] = []
    buffer = ""
    for paragraph in paragraphs:
        candidate = f"{buffer}\n\n{paragraph}" if buffer else paragraph
        if len(candidate) <= max_chars:
            buffer = candidate
            continue

        if buffer:
            chunks.append(_make_chunk(document, buffer, len(chunks)))
            buffer = ""

        if len(paragraph) <= max_chars:
            buffer = paragraph
        else:
            for start in range(0, len(paragraph), max_chars):
                piece = paragraph[start:start + max_chars]
                chunks.append(_make_chunk(document, piece, len(chunks)))

    if buffer:
        chunks.append(_make_chunk(document, buffer, len(chunks)))

    return chunks


def _make_chunk(document: Document, text: str, position: int) -> Chunk:
    return Chunk(
        chunk_id=f"{document.doc_id}:{position}",
        doc_id=document.doc_id,
        doc_title=document.title,
        corpus=document.corpus,
        position=position,
        text=text,
        metadata=document.metadata,
    )
