import pytest
from approvals import ApprovalQueue, Decision, Role
from audit_log import AuditLogStore
from fakes import refusal_response, text_response
from fixtures import ALL_DOCUMENTS, SOFTWARE_CAPITALIZATION_STANDARD
from knowledge import KnowledgeBase
from technical_accounting_agent import answer_question

TS = iter(f"2026-08-01T00:0{i}:00Z" for i in range(10))


def fixed_now():
    return next(TS)


@pytest.fixture
def knowledge_base():
    kb = KnowledgeBase()
    kb.ingest(ALL_DOCUMENTS)
    return kb


@pytest.fixture
def audit_log(tmp_path):
    store = AuditLogStore(tmp_path / "audit.db")
    yield store
    store.close()


@pytest.fixture
def approval_queue(tmp_path, audit_log):
    queue = ApprovalQueue(tmp_path / "approvals.db", audit_log)
    yield queue
    queue.close()


def test_end_to_end_answer_flows_to_approved(knowledge_base, audit_log, approval_queue):
    client = text_response(
        "Payroll costs are capitalized once the application development stage begins "
        '(Capitalization of Internal-Use Software Costs (Synthetic Fixture) (standard), chunk 1).'
    )

    run = answer_question(
        question="When can payroll costs for internal-use software be capitalized?",
        knowledge_base=knowledge_base,
        audit_log=audit_log,
        approval_queue=approval_queue,
        client=client,
        now=fixed_now,
    )

    assert run.draft.refused is False
    assert "capitalized" in run.draft.answer_text
    assert run.draft.chunk_ids  # retrieval actually found something
    assert run.draft.model == "claude-sonnet-5"

    request = run.approval_request
    assert request is not None
    assert request.status == "pending"
    assert request.current_stage == Role.REVIEWER.value
    assert request.preparer == "technical-accounting-agent"
    assert request.payload["answer_text"] == run.draft.answer_text
    assert request.payload["citations"] == run.draft.citations

    # Walk the approval chain: reviewer, then approver.
    reviewed = approval_queue.decide(
        request.id, actor="alice", role=Role.REVIEWER, decision=Decision.APPROVE,
        timestamp=fixed_now(),
    )
    assert reviewed.current_stage == Role.APPROVER.value

    approved = approval_queue.decide(
        request.id, actor="bob", role=Role.APPROVER, decision=Decision.APPROVE,
        timestamp=fixed_now(),
    )
    assert approved.status == "approved"

    # Full audit trail: retrieval, drafting, submission, and both decisions.
    events = audit_log.get_all()
    actions = [e.action for e in events]
    assert actions == [
        "chunks_retrieved",
        "answer_drafted",
        "approval_submitted:technical_accounting_answer",
        "approval_reviewer_approve:technical_accounting_answer",
        "approval_approver_approve:technical_accounting_answer",
    ]

    retrieved_event = events[0]
    assert retrieved_event.output["chunks"]
    assert retrieved_event.output["chunks"][0]["doc_id"] == SOFTWARE_CAPITALIZATION_STANDARD.doc_id

    drafted_event = events[1]
    assert drafted_event.model == "claude-sonnet-5"
    assert drafted_event.prompt_hash == run.draft.prompt_hash
    assert drafted_event.output["answer_text"] == run.draft.answer_text

    assert audit_log.verify_chain().ok is True


def test_a_refusal_is_logged_but_never_reaches_approvals(knowledge_base, audit_log, approval_queue):
    client = refusal_response(category="cyber")

    run = answer_question(
        question="irrelevant question",
        knowledge_base=knowledge_base,
        audit_log=audit_log,
        approval_queue=approval_queue,
        client=client,
        now=fixed_now,
    )

    assert run.draft.refused is True
    assert run.approval_request is None
    assert approval_queue.list_pending() == []

    actions = [e.action for e in audit_log.get_all()]
    assert actions == ["chunks_retrieved", "answer_refused"]
    assert audit_log.verify_chain().ok is True
