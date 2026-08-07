"""Orchestrates one technical-accounting Q&A: retrieval -> LLM draft -> approvals.

Every step writes to the audit log, including which chunks were retrieved and
the model/prompt hash used to draft the answer, so the run can be
reconstructed as auditor evidence (CLAUDE.md golden rule #3). The agent never
queries a document store directly — all retrieval goes through
platform/knowledge.KnowledgeBase (rule #1) — and never treats a drafted
answer as final without a human approval (rule #2): this function either
submits a draft for human review, or, on a safety refusal, submits nothing.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from approvals import ApprovalQueue, ApprovalRequest
from audit_log import AuditEvent, AuditLogStore
from knowledge import KnowledgeBase

from . import llm
from .models import AnswerDraft, serialize_search_results

AGENT_NAME = "technical-accounting-agent"
DEFAULT_TOP_K = 5


def _default_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AnswerRun:
    draft: AnswerDraft
    approval_request: Optional[ApprovalRequest]


def answer_question(
    *,
    question: str,
    knowledge_base: KnowledgeBase,
    audit_log: AuditLogStore,
    approval_queue: ApprovalQueue,
    client=None,
    model: str = llm.DEFAULT_MODEL,
    effort: str = llm.DEFAULT_EFFORT,
    top_k: int = DEFAULT_TOP_K,
    preparer: str = AGENT_NAME,
    now: Callable[[], str] = _default_now,
) -> AnswerRun:
    chunks = knowledge_base.search(question, top_k=top_k)

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="chunks_retrieved",
        actor=preparer,
        inputs={"question": question, "top_k": top_k},
        output={"chunks": serialize_search_results(chunks)},
    ))

    if client is None:
        import anthropic
        client = anthropic.Anthropic()

    draft = llm.draft_answer(client, question, chunks, model=model, effort=effort)

    if draft.refused:
        audit_log.append(AuditEvent(
            timestamp=now(),
            agent=AGENT_NAME,
            action="answer_refused",
            actor=preparer,
            inputs={"question": question, "chunk_ids": draft.chunk_ids},
            output={"refusal_category": draft.refusal_category},
            model=model,
            prompt_hash=draft.prompt_hash,
        ))
        return AnswerRun(draft=draft, approval_request=None)

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="answer_drafted",
        actor=preparer,
        inputs={"question": question, "chunk_ids": draft.chunk_ids},
        output={"answer_text": draft.answer_text, "citations": draft.citations},
        model=model,
        prompt_hash=draft.prompt_hash,
    ))

    approval_request = approval_queue.submit(
        agent=AGENT_NAME,
        action="technical_accounting_answer",
        payload=draft.to_dict(),
        preparer=preparer,
        timestamp=now(),
    )

    return AnswerRun(draft=draft, approval_request=approval_request)
