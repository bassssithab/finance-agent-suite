"""Data model for a PBC item, its tie-out result, and a drafted response.

A TieOutResult is deterministic evidence-lookup output — no LLM involvement
(CLAUDE.md golden rule #4). A PBCResponseDraft is never the final answer by
itself — rule #2 requires every drafted response to go through
platform/approvals before it is considered final. This module only shapes
these values and their audit/approval payloads; tie-out happens in
tie_out.py, drafting in llm.py, orchestration in runner.py.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from knowledge import SearchResult


@dataclass(frozen=True)
class PBCItem:
    """One structured PBC (Prepared-By-Client) request from an auditor.

    `description` is auditor-facing free text used only as LLM context and a
    knowledge-search query — tie-out matching runs on the structured fields
    below, never on this text, so matching stays deterministic code.
    """

    item_id: str
    description: str
    period_start: date
    period_end: date
    evidence_type: str
    source_system: Optional[str] = None


@dataclass(frozen=True)
class TieOutEntry:
    """One piece of evidence tied out to a PBC item, citing its source by
    audit-event id rather than copying the event itself (see
    audit_readiness_agent README, "Evidence sourcing: cite, don't mirror")."""

    audit_event_ids: list[int]
    evidence_agent: str
    period_start: Optional[date]
    period_end: Optional[date]
    source_system: Optional[str]
    summary: dict
    approval_status: Optional[str]

    def to_dict(self) -> dict:
        return {
            "audit_event_ids": self.audit_event_ids,
            "evidence_agent": self.evidence_agent,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "source_system": self.source_system,
            "summary": self.summary,
            "approval_status": self.approval_status,
        }


@dataclass(frozen=True)
class TieOutResult:
    pbc_item_id: str
    found: bool
    entries: list[TieOutEntry] = field(default_factory=list)
    gap_reason: Optional[str] = None
    evidence_source_db_path: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "pbc_item_id": self.pbc_item_id,
            "found": self.found,
            "entries": [e.to_dict() for e in self.entries],
            "gap_reason": self.gap_reason,
            "evidence_source_db_path": self.evidence_source_db_path,
        }


@dataclass(frozen=True)
class PBCResponseDraft:
    item_id: str
    model: str
    prompt_hash: str
    tie_out_found: bool
    evidence_event_ids: list[int]
    knowledge_chunk_ids: list[str]
    citations: list[str]
    response_text: Optional[str] = None
    refused: bool = False
    refusal_category: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "model": self.model,
            "tie_out_found": self.tie_out_found,
            "evidence_event_ids": self.evidence_event_ids,
            "knowledge_chunk_ids": self.knowledge_chunk_ids,
            "citations": self.citations,
            "response_text": self.response_text,
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
