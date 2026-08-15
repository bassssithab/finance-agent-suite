"""Orchestrates one PBC response: tie-out -> (optional) knowledge -> LLM draft -> approvals.

Every step writes to this agent's own audit log, including which evidence
audit-log events were tied out and the model/prompt hash used to draft the
response, so the run can be reconstructed as auditor evidence (CLAUDE.md
golden rule #3). The agent never mirrors another agent's raw audit events
into its own chain (see README, "Evidence sourcing: cite, don't mirror") and
never treats a drafted response as final without a human approval (rule #2):
this function either submits a draft for human review, or, on a safety
refusal, submits nothing.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from approvals import ApprovalQueue, ApprovalRequest
from audit_log import AuditEvent, AuditLogStore
from knowledge import KnowledgeBase

from . import llm
from .models import PBCItem, PBCResponseDraft, TieOutResult, serialize_search_results
from .tie_out import find_evidence

AGENT_NAME = "audit-readiness-agent"
DEFAULT_TOP_K = 5


def _default_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PBCResponseRun:
    tie_out: TieOutResult
    draft: PBCResponseDraft
    approval_request: Optional[ApprovalRequest]


def respond_to_pbc_item(
    *,
    pbc_item: PBCItem,
    evidence_audit_log: AuditLogStore,
    audit_log: AuditLogStore,
    approval_queue: ApprovalQueue,
    knowledge_base: Optional[KnowledgeBase] = None,
    client=None,
    model: str = llm.DEFAULT_MODEL,
    effort: str = llm.DEFAULT_EFFORT,
    top_k: int = DEFAULT_TOP_K,
    preparer: str = AGENT_NAME,
    now: Callable[[], str] = _default_now,
) -> PBCResponseRun:
    tie_out_result = find_evidence(pbc_item, evidence_audit_log)

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="evidence_tied_out",
        actor=preparer,
        inputs={
            "item_id": pbc_item.item_id,
            "period_start": pbc_item.period_start.isoformat(),
            "period_end": pbc_item.period_end.isoformat(),
            "evidence_type": pbc_item.evidence_type,
            "source_system": pbc_item.source_system,
        },
        output=tie_out_result.to_dict(),
    ))

    knowledge_chunks = []
    if knowledge_base is not None:
        knowledge_chunks = knowledge_base.search(pbc_item.description, top_k=top_k)
        audit_log.append(AuditEvent(
            timestamp=now(),
            agent=AGENT_NAME,
            action="chunks_retrieved",
            actor=preparer,
            inputs={"query": pbc_item.description, "top_k": top_k},
            output={"chunks": serialize_search_results(knowledge_chunks)},
        ))

    if client is None:
        import anthropic
        client = anthropic.Anthropic()

    draft = llm.draft_response(client, pbc_item, tie_out_result, knowledge_chunks, model=model, effort=effort)

    if draft.refused:
        audit_log.append(AuditEvent(
            timestamp=now(),
            agent=AGENT_NAME,
            action="response_refused",
            actor=preparer,
            inputs={"item_id": pbc_item.item_id, "evidence_event_ids": draft.evidence_event_ids},
            output={"refusal_category": draft.refusal_category},
            model=model,
            prompt_hash=draft.prompt_hash,
        ))
        return PBCResponseRun(tie_out=tie_out_result, draft=draft, approval_request=None)

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="response_drafted",
        actor=preparer,
        inputs={"item_id": pbc_item.item_id, "evidence_event_ids": draft.evidence_event_ids},
        output={"response_text": draft.response_text, "citations": draft.citations},
        model=model,
        prompt_hash=draft.prompt_hash,
    ))

    approval_request = approval_queue.submit(
        agent=AGENT_NAME,
        action="pbc_response",
        payload=draft.to_dict(),
        preparer=preparer,
        timestamp=now(),
    )

    return PBCResponseRun(tie_out=tie_out_result, draft=draft, approval_request=approval_request)
