"""Data model for a drafted technical-accounting answer.

An AnswerDraft is never the final answer by itself — CLAUDE.md rule #2
requires every drafted answer to go through platform/approvals before it is
considered final. This module only shapes the draft and its audit/approval
payloads; drafting happens in llm.py, orchestration in runner.py.
"""

from dataclasses import dataclass, field
from typing import Optional

from knowledge import SearchResult


@dataclass(frozen=True)
class AnswerDraft:
    question: str
    model: str
    prompt_hash: str
    chunk_ids: list[str]
    citations: list[str]
    answer_text: Optional[str] = None
    refused: bool = False
    refusal_category: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "model": self.model,
            "chunk_ids": self.chunk_ids,
            "citations": self.citations,
            "answer_text": self.answer_text,
            "refused": self.refused,
            "refusal_category": self.refusal_category,
        }


def serialize_search_results(results: list[SearchResult]) -> list[dict]:
    """JSON-safe view of retrieved chunks for the chunks_retrieved audit event."""
    return [
        {
            "chunk_id": r.chunk.chunk_id,
            "doc_id": r.chunk.doc_id,
            "citation": r.chunk.citation,
            "score": r.score,
            "text": r.chunk.text,
        }
        for r in results
    ]
