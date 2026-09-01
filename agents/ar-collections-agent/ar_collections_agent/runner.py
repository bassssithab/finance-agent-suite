"""Orchestrates one AR aging + dunning run: connector -> deterministic aging and
flagging -> optional cited dunning-email drafts -> approvals.

Every step writes to the audit log, including which invoices were flagged and
the deterministic reason each was flagged, so the run can be reconstructed as
auditor evidence (CLAUDE.md golden rule #3). The agent never reads the
open-invoices CSV itself — it goes through `platform/connectors` (rule #1) — and
never treats the report as final without a human approval (rule #2): this
function only ever produces a draft collections report for review, and no email
is ever sent.

`autonomy: draft_only`. Flagged invoices do NOT stop submission — the report
still goes to a human, carrying the flags and the drafted emails, because the
agent does not get to decide (rule #2). A model refusal or an unparseable
response also does not stop submission: the report goes through without drafts,
with `drafts_skipped_reason` set.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

from approvals import ApprovalQueue, ApprovalRequest
from audit_log import AuditEvent, AuditLogStore
from connectors import FileOpenInvoiceConnector, OpenInvoice
from knowledge import KnowledgeBase, SearchResult

from . import draft
from .aging import compute_aging
from .draft import DunningResult, describe_invoice
from .models import CollectionsReport, DunningDraft, DunningPolicy

AGENT_NAME = "ar-collections-agent"
KNOWLEDGE_CORPUS = "collections_policy"
DEFAULT_RETRIEVAL_TOP_K = 3


def _default_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize_invoice(inv: OpenInvoice) -> dict:
    return {
        "source_capability": inv.source_capability,
        "invoice_id": inv.invoice_id,
        "customer": inv.customer,
        "invoice_date": inv.invoice_date.isoformat(),
        "due_date": inv.due_date.isoformat(),
        "amount": str(inv.amount),
        "currency": inv.currency,
        "last_payment_date": (
            inv.last_payment_date.isoformat() if inv.last_payment_date is not None else None
        ),
        "raw": inv.raw,
    }


@dataclass
class ARCollectionsRun:
    report: CollectionsReport
    approval_request: ApprovalRequest
    draft_result: Optional[DunningResult]  # None when nothing was flagged


def _retrieve_context(
    knowledge_base: KnowledgeBase, flagged, top_k: int
) -> list[SearchResult]:
    """One search per flagged invoice, de-duplicated by chunk_id, order preserved
    (same shape as close_agent._retrieve_context)."""
    seen: set[str] = set()
    chunks: list[SearchResult] = []
    for ia in flagged:
        for result in knowledge_base.search(describe_invoice(ia), top_k=top_k):
            if result.chunk.chunk_id not in seen:
                seen.add(result.chunk.chunk_id)
                chunks.append(result)
    return chunks


def run_ar_collections_analysis(
    *,
    source_system: str,
    invoices_folder: Union[str, Path],
    knowledge_base: KnowledgeBase,
    audit_log: AuditLogStore,
    approval_queue: ApprovalQueue,
    as_of_date: Optional[date] = None,
    client=None,
    model: str = draft.DEFAULT_MODEL,
    effort: str = draft.DEFAULT_EFFORT,
    policy: Optional[DunningPolicy] = None,
    retrieval_top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    preparer: str = AGENT_NAME,
    now: Callable[[], str] = _default_now,
) -> ARCollectionsRun:
    if policy is None:
        policy = DunningPolicy()
    if as_of_date is None:
        as_of_date = date.today()

    connector = FileOpenInvoiceConnector(source_system=source_system, folder=invoices_folder)
    invoices = connector.fetch_invoices()

    if not invoices:
        raise ValueError(f"no open invoices found in {invoices_folder!r}")

    currencies = sorted({inv.currency for inv in invoices})
    if len(currencies) > 1:
        raise ValueError(
            f"open invoices mix currencies {currencies}; "
            "multi-currency aging is out of scope"
        )
    currency = currencies[0]

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="open_invoices_retrieved",
        actor=preparer,
        inputs={
            "source_system": source_system,
            "invoices_folder": str(invoices_folder),
            "as_of_date": as_of_date.isoformat(),
        },
        output={
            "invoice_count": len(invoices),
            "currency": currency,
            "invoices": [_serialize_invoice(inv) for inv in invoices],
        },
    ))

    agings = compute_aging(invoices, as_of_date=as_of_date, policy=policy)
    flagged = [ia for ia in agings if ia.flagged]

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="aging_computed",
        actor=preparer,
        inputs={"as_of_date": as_of_date.isoformat(), "policy": policy.to_dict()},
        output={
            "invoice_count": len(agings),
            "flagged_count": len(flagged),
            "aged": [
                {
                    "invoice_id": ia.invoice_id,
                    "customer": ia.customer,
                    "days_overdue": ia.days_overdue,
                    "bucket": ia.bucket,
                }
                for ia in agings
            ],
            "flagged": [
                {
                    "invoice_id": ia.invoice_id,
                    "customer": ia.customer,
                    "amount": str(ia.amount),
                    "days_overdue": ia.days_overdue,
                    "bucket": ia.bucket,
                    "tone_tier": ia.tone_tier,
                    "flag_reasons": ia.flag_reasons,
                }
                for ia in flagged
            ],
        },
    ))

    drafts: list[DunningDraft] = []
    draft_result: Optional[DunningResult] = None
    skipped_reason: Optional[str] = None
    draft_model: Optional[str] = None
    draft_prompt_hash: Optional[str] = None
    draft_chunk_ids: list[str] = []
    draft_citations: list[str] = []

    if not flagged:
        skipped_reason = "no_flagged_invoices"
        audit_log.append(AuditEvent(
            timestamp=now(),
            agent=AGENT_NAME,
            action="dunning_drafts_skipped",
            actor=preparer,
            inputs={},
            output={"reason": skipped_reason},
        ))
    else:
        chunks = _retrieve_context(knowledge_base, flagged, retrieval_top_k)
        draft_chunk_ids = [r.chunk.chunk_id for r in chunks]
        draft_citations = [r.chunk.citation for r in chunks]

        audit_log.append(AuditEvent(
            timestamp=now(),
            agent=AGENT_NAME,
            action="dunning_context_retrieved",
            actor=preparer,
            inputs={"flagged_count": len(flagged), "top_k": retrieval_top_k},
            output={
                "grounded": bool(chunks),
                "chunk_ids": draft_chunk_ids,
                "citations": draft_citations,
            },
        ))

        if client is None:
            import anthropic
            client = anthropic.Anthropic()

        draft_result = draft.draft_dunning_emails(
            client, flagged, chunks, model=model, effort=effort
        )
        draft_model = draft_result.model
        draft_prompt_hash = draft_result.prompt_hash

        if draft_result.drafts is None:
            if draft_result.refused:
                skipped_reason = "dunning_drafts_refused"
                action = "dunning_drafts_refused"
                output = {"refusal_category": draft_result.refusal_category}
            else:
                skipped_reason = "dunning_drafts_failed"
                action = "dunning_drafts_failed"
                output = {"parse_error": draft_result.parse_error}
            audit_log.append(AuditEvent(
                timestamp=now(),
                agent=AGENT_NAME,
                action=action,
                actor=preparer,
                inputs={"chunk_ids": draft_chunk_ids},
                output=output,
                model=draft_model,
                prompt_hash=draft_prompt_hash,
            ))
        else:
            drafts = draft_result.drafts
            audit_log.append(AuditEvent(
                timestamp=now(),
                agent=AGENT_NAME,
                action="dunning_drafts_drafted",
                actor=preparer,
                inputs={"chunk_ids": draft_chunk_ids},
                output={
                    "citations": draft_citations,
                    "drafts": [d.to_dict() for d in drafts],
                },
                model=draft_model,
                prompt_hash=draft_prompt_hash,
            ))

    report = CollectionsReport(
        as_of_date=as_of_date.isoformat(),
        source_system=source_system,
        generated_at=now(),
        currency=currency,
        policy=policy.to_dict(),
        invoice_agings=agings,
        drafts=drafts,
        drafts_skipped_reason=skipped_reason,
        model=draft_model,
        draft_prompt_hash=draft_prompt_hash,
        draft_chunk_ids=draft_chunk_ids,
        draft_citations=draft_citations,
    )
    report_dict = report.to_dict()

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="collections_report_generated",
        actor=preparer,
        inputs={},
        output=report_dict["summary"],
    ))

    approval_request = approval_queue.submit(
        agent=AGENT_NAME,
        action="collections_report",
        payload=report_dict,
        preparer=preparer,
        timestamp=now(),
    )

    return ARCollectionsRun(
        report=report,
        approval_request=approval_request,
        draft_result=draft_result,
    )
