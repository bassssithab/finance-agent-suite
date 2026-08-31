"""Orchestrates one AP invoice: document -> vision extraction -> deterministic
sanity check -> optional GL coding -> approvals.

Every step writes to the audit log, including the extraction confidence and any
discrepancy the arithmetic check flags, so a run can be reconstructed as
auditor evidence (CLAUDE.md golden rule #3). The agent never reads the image
off disk itself — it goes through a `platform/connectors.DocumentConnector`
(rule #1) — and never treats the drafted invoice as final without a human
approval (rule #2): this function either submits a draft for review, or, when
extraction fails/refuses, submits nothing.

`autonomy: draft_only`. A flagged discrepancy does NOT stop submission — the
draft still goes to a human, carrying the flag, because the agent doesn't get
to decide (rule #2).
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from approvals import ApprovalQueue, ApprovalRequest
from audit_log import AuditEvent, AuditLogStore
from connectors import DocumentConnector
from knowledge import KnowledgeBase, SearchResult

from . import coding, extraction
from .models import InvoiceDraft
from .sanity import check_invoice_totals

AGENT_NAME = "ap-agent"
CODING_CORPUS = "chart_of_accounts"
DEFAULT_CODING_TOP_K = 3


def _default_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ApInvoiceRun:
    draft: Optional[InvoiceDraft]
    approval_request: Optional[ApprovalRequest]
    extraction: extraction.ExtractionResult


def _retrieve_coding_context(
    knowledge_base: KnowledgeBase, invoice, top_k: int
) -> list[SearchResult]:
    """One search per line item, de-duplicated by chunk_id, order preserved."""
    seen: set[str] = set()
    chunks: list[SearchResult] = []
    for li in invoice.line_items:
        for result in knowledge_base.search(li.description, top_k=top_k):
            if result.chunk.chunk_id not in seen:
                seen.add(result.chunk.chunk_id)
                chunks.append(result)
    return chunks


def process_invoice(
    *,
    document_id: str,
    document_connector: DocumentConnector,
    knowledge_base: KnowledgeBase,
    audit_log: AuditLogStore,
    approval_queue: ApprovalQueue,
    client=None,
    model: str = extraction.DEFAULT_MODEL,
    effort: str = extraction.DEFAULT_EFFORT,
    coding_top_k: int = DEFAULT_CODING_TOP_K,
    preparer: str = AGENT_NAME,
    now: Callable[[], str] = _default_now,
) -> ApInvoiceRun:
    document = document_connector.fetch_document(document_id)
    source_document = {
        "document_id": document.document_id,
        "filename": document.filename,
        "media_type": document.media_type,
        "sha256": document.sha256,
        "size_bytes": document.size_bytes,
    }

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="invoice_document_received",
        actor=preparer,
        inputs={"document_id": document_id},
        output=source_document,
    ))

    if client is None:
        import anthropic
        client = anthropic.Anthropic()

    extraction_result = extraction.extract_invoice(
        client, content=document.content, media_type=document.media_type,
        model=model, effort=effort,
    )

    if not extraction_result.ok:
        audit_log.append(AuditEvent(
            timestamp=now(),
            agent=AGENT_NAME,
            action="invoice_extraction_failed",
            actor=preparer,
            inputs={"document_id": document_id},
            output={
                "refused": extraction_result.refused,
                "refusal_category": extraction_result.refusal_category,
                "parse_error": extraction_result.parse_error,
            },
            model=model,
            prompt_hash=extraction_result.prompt_hash,
        ))
        return ApInvoiceRun(
            draft=None, approval_request=None, extraction=extraction_result
        )

    invoice = extraction_result.invoice
    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="invoice_extracted",
        actor=preparer,
        inputs={"document_id": document_id},
        output={
            "vendor_name": invoice.vendor_name,
            "invoice_number": invoice.invoice_number,
            "invoice_date": invoice.invoice_date,
            "currency": invoice.currency,
            "line_item_count": len(invoice.line_items),
            "grand_total": str(invoice.grand_total),
            "extraction_confidence": invoice.extraction_confidence,
        },
        model=model,
        prompt_hash=extraction_result.prompt_hash,
    ))

    sanity = check_invoice_totals(invoice)
    discrepancy_flagged = not sanity.ok
    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="sanity_check_completed",
        actor=preparer,
        inputs={"document_id": document_id},
        output={"discrepancy_flagged": discrepancy_flagged, **sanity.to_dict()},
    ))

    # ---- optional GL coding -------------------------------------------------
    chunks = _retrieve_coding_context(knowledge_base, invoice, coding_top_k)
    gl_suggestions = []
    coding_skipped_reason: Optional[str] = None
    coding_prompt_hash: Optional[str] = None
    coding_chunk_ids: list[str] = []
    coding_citations: list[str] = []

    if not chunks:
        coding_skipped_reason = "no_relevant_knowledge"
        audit_log.append(AuditEvent(
            timestamp=now(),
            agent=AGENT_NAME,
            action="gl_coding_skipped",
            actor=preparer,
            inputs={"document_id": document_id},
            output={"reason": coding_skipped_reason},
        ))
    else:
        coding_result = coding.suggest_coding(
            client, invoice, chunks, model=model, effort=effort
        )
        coding_prompt_hash = coding_result.prompt_hash
        coding_chunk_ids = coding_result.chunk_ids
        coding_citations = coding_result.citations

        if coding_result.suggestions is None:
            coding_skipped_reason = "coding_failed"
            audit_log.append(AuditEvent(
                timestamp=now(),
                agent=AGENT_NAME,
                action="gl_coding_failed",
                actor=preparer,
                inputs={"document_id": document_id, "chunk_ids": coding_chunk_ids},
                output={
                    "refused": coding_result.refused,
                    "refusal_category": coding_result.refusal_category,
                    "parse_error": coding_result.parse_error,
                },
                model=model,
                prompt_hash=coding_prompt_hash,
            ))
        else:
            gl_suggestions = coding_result.suggestions
            audit_log.append(AuditEvent(
                timestamp=now(),
                agent=AGENT_NAME,
                action="gl_coding_suggested",
                actor=preparer,
                inputs={"document_id": document_id, "chunk_ids": coding_chunk_ids},
                output={
                    "citations": coding_citations,
                    "suggestions": [s.to_dict() for s in gl_suggestions],
                },
                model=model,
                prompt_hash=coding_prompt_hash,
            ))

    draft = InvoiceDraft(
        invoice=invoice,
        sanity_check=sanity,
        discrepancy_flagged=discrepancy_flagged,
        gl_suggestions=gl_suggestions,
        coding_skipped_reason=coding_skipped_reason,
        model=model,
        extraction_prompt_hash=extraction_result.prompt_hash,
        coding_prompt_hash=coding_prompt_hash,
        coding_chunk_ids=coding_chunk_ids,
        coding_citations=coding_citations,
        source_document=source_document,
    )

    approval_request = approval_queue.submit(
        agent=AGENT_NAME,
        action="ap_invoice_coding",
        payload=draft.to_dict(),
        preparer=preparer,
        timestamp=now(),
    )

    return ApInvoiceRun(
        draft=draft, approval_request=approval_request, extraction=extraction_result
    )
