"""Orchestrates one expense receipt: document -> vision extraction -> deterministic
policy compliance check -> optional cited explanation -> approvals.

Every step writes to the audit log, including the extraction confidence and
every policy violation with its deterministic reason, so a run can be
reconstructed as auditor evidence (CLAUDE.md golden rule #3). The agent never
reads the image off disk itself — it goes through a
`platform/connectors.DocumentConnector` (rule #1) — and never treats the drafted
expense as final without a human approval (rule #2): this function either
submits a draft for review, or, when extraction fails/refuses, submits nothing.

`autonomy: draft_only`. A flagged policy violation does NOT stop submission —
the draft still goes to a human, carrying the flag and any drafted explanation,
because the agent doesn't get to decide (rule #2). A model refusal or an
unparseable response on the explanation step also does not stop submission: the
draft goes through without explanations, with `explanation_skipped_reason` set.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, Optional

from approvals import ApprovalQueue, ApprovalRequest
from audit_log import AuditEvent, AuditLogStore
from connectors import DocumentConnector
from knowledge import KnowledgeBase, SearchResult

from . import explain, extraction
from .compliance import check_compliance
from .models import ExpenseDraft, ExpensePolicy, PolicyExplanation

AGENT_NAME = "expense-agent"
POLICY_CORPUS = "expense_policy"
DEFAULT_RETRIEVAL_TOP_K = 3


def _default_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ExpenseRun:
    draft: Optional[ExpenseDraft]
    approval_request: Optional[ApprovalRequest]
    extraction: extraction.ExtractionResult


def _retrieve_context(
    knowledge_base: KnowledgeBase, receipt, violations, top_k: int
) -> list[SearchResult]:
    """One search per violation, de-duplicated by chunk_id, order preserved
    (same shape as ap_agent._retrieve_coding_context)."""
    seen: set[str] = set()
    chunks: list[SearchResult] = []
    for violation in violations:
        query = explain.describe_violation(violation, receipt)
        for result in knowledge_base.search(query, top_k=top_k):
            if result.chunk.chunk_id not in seen:
                seen.add(result.chunk.chunk_id)
                chunks.append(result)
    return chunks


def check_receipt_policy_compliance(
    *,
    document_id: str,
    document_connector: DocumentConnector,
    knowledge_base: KnowledgeBase,
    audit_log: AuditLogStore,
    approval_queue: ApprovalQueue,
    policy: Optional[ExpensePolicy] = None,
    as_of_date: Optional[date] = None,
    client=None,
    model: str = extraction.DEFAULT_MODEL,
    effort: str = extraction.DEFAULT_EFFORT,
    retrieval_top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    preparer: str = AGENT_NAME,
    now: Callable[[], str] = _default_now,
) -> ExpenseRun:
    if policy is None:
        policy = ExpensePolicy()
    if as_of_date is None:
        as_of_date = date.today()

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
        action="receipt_document_received",
        actor=preparer,
        inputs={"document_id": document_id},
        output=source_document,
    ))

    if client is None:
        import anthropic
        client = anthropic.Anthropic()

    extraction_result = extraction.extract_receipt(
        client, content=document.content, media_type=document.media_type,
        model=model, effort=effort,
    )

    if not extraction_result.ok:
        audit_log.append(AuditEvent(
            timestamp=now(),
            agent=AGENT_NAME,
            action="receipt_extraction_failed",
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
        return ExpenseRun(
            draft=None, approval_request=None, extraction=extraction_result
        )

    receipt = extraction_result.receipt
    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="receipt_extracted",
        actor=preparer,
        inputs={"document_id": document_id},
        output={
            "vendor": receipt.vendor,
            "date": receipt.date,
            "amount": str(receipt.amount),
            "currency": receipt.currency,
            "expense_category": receipt.expense_category,
            "extraction_confidence": receipt.extraction_confidence,
        },
        model=model,
        prompt_hash=extraction_result.prompt_hash,
    ))

    compliance = check_compliance(receipt, policy, as_of_date=as_of_date)
    compliance_flagged = not compliance.passed
    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="compliance_check_completed",
        actor=preparer,
        inputs={"document_id": document_id, "policy": policy.to_dict()},
        output={"compliance_flagged": compliance_flagged, **compliance.to_dict()},
    ))

    # ---- optional cited explanation of the flagged violations --------------
    explanations: list[PolicyExplanation] = []
    explanation_skipped_reason: Optional[str] = None
    explanation_model: Optional[str] = None
    explanation_prompt_hash: Optional[str] = None
    explanation_chunk_ids: list[str] = []
    explanation_citations: list[str] = []

    if not compliance.violations:
        explanation_skipped_reason = "no_violations"
        audit_log.append(AuditEvent(
            timestamp=now(),
            agent=AGENT_NAME,
            action="policy_explanation_skipped",
            actor=preparer,
            inputs={"document_id": document_id},
            output={"reason": explanation_skipped_reason},
        ))
    else:
        chunks = _retrieve_context(
            knowledge_base, receipt, compliance.violations, retrieval_top_k
        )
        if not chunks:
            explanation_skipped_reason = "no_relevant_knowledge"
            audit_log.append(AuditEvent(
                timestamp=now(),
                agent=AGENT_NAME,
                action="policy_explanation_skipped",
                actor=preparer,
                inputs={"document_id": document_id},
                output={"reason": explanation_skipped_reason},
            ))
        else:
            explanation_chunk_ids = [r.chunk.chunk_id for r in chunks]
            explanation_citations = [r.chunk.citation for r in chunks]
            result = explain.draft_policy_explanations(
                client, receipt, compliance.violations, chunks, model=model, effort=effort
            )
            explanation_model = result.model
            explanation_prompt_hash = result.prompt_hash

            if result.explanations is None:
                if result.refused:
                    explanation_skipped_reason = "explanation_refused"
                    output = {"refused": True, "refusal_category": result.refusal_category}
                else:
                    explanation_skipped_reason = "explanation_failed"
                    output = {"refused": False, "parse_error": result.parse_error}
                audit_log.append(AuditEvent(
                    timestamp=now(),
                    agent=AGENT_NAME,
                    action="policy_explanation_failed",
                    actor=preparer,
                    inputs={"document_id": document_id, "chunk_ids": explanation_chunk_ids},
                    output=output,
                    model=explanation_model,
                    prompt_hash=explanation_prompt_hash,
                ))
            else:
                explanations = result.explanations
                audit_log.append(AuditEvent(
                    timestamp=now(),
                    agent=AGENT_NAME,
                    action="policy_explanation_drafted",
                    actor=preparer,
                    inputs={"document_id": document_id, "chunk_ids": explanation_chunk_ids},
                    output={
                        "citations": explanation_citations,
                        "explanations": [e.to_dict() for e in explanations],
                    },
                    model=explanation_model,
                    prompt_hash=explanation_prompt_hash,
                ))

    draft = ExpenseDraft(
        receipt=receipt,
        compliance=compliance,
        compliance_flagged=compliance_flagged,
        explanations=explanations,
        explanation_skipped_reason=explanation_skipped_reason,
        model=model,
        extraction_prompt_hash=extraction_result.prompt_hash,
        explanation_prompt_hash=explanation_prompt_hash,
        explanation_chunk_ids=explanation_chunk_ids,
        explanation_citations=explanation_citations,
        source_document=source_document,
    )

    approval_request = approval_queue.submit(
        agent=AGENT_NAME,
        action="expense_policy_check",
        payload=draft.to_dict(),
        preparer=preparer,
        timestamp=now(),
    )

    return ExpenseRun(
        draft=draft, approval_request=approval_request, extraction=extraction_result
    )
