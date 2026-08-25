"""Data model for a drafted VAT-treatment answer.

A VatTreatmentDraft is never a final VAT position by itself — CLAUDE.md rule
#2 requires every drafted answer to go through platform/approvals before it
is considered final. This module only shapes the input line item, the draft,
and its audit/approval payloads; drafting happens in llm.py, orchestration in
runner.py.
"""

from dataclasses import dataclass
from typing import Optional

from knowledge import SearchResult


@dataclass(frozen=True)
class InvoiceLineItem:
    goods_type: str
    customer_location: str
    transaction_type: str


def describe_line_item(item: InvoiceLineItem) -> str:
    """Deterministic natural-language description of a line item.

    Used both as the platform/knowledge search query and embedded in the
    LLM prompt's question — CLAUDE.md rule #4: this is the plain-code half
    that assembles facts, the LLM only classifies/explains from what it's
    given.
    """
    return (
        f"Invoice line item: goods type = {item.goods_type}; "
        f"customer location = {item.customer_location}; "
        f"transaction type = {item.transaction_type}. "
        "Which VAT treatment applies (standard-rated, zero-rated export, "
        "exempt, or out-of-scope), and why?"
    )


@dataclass(frozen=True)
class VatTreatmentDraft:
    line_item: InvoiceLineItem
    model: str
    prompt_hash: str
    chunk_ids: list[str]
    citations: list[str]
    answer_text: Optional[str] = None
    refused: bool = False
    refusal_category: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "goods_type": self.line_item.goods_type,
            "customer_location": self.line_item.customer_location,
            "transaction_type": self.line_item.transaction_type,
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
