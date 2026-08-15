from datetime import date

import pytest
from approvals import ApprovalQueue, Decision, Role
from audit_log import AuditLogStore
from audit_readiness_agent import PBCItem, respond_to_pbc_item
from fakes import refusal_response, text_response
from fixtures import ALL_DOCUMENTS, build_evidence_log
from knowledge import KnowledgeBase

@pytest.fixture
def fixed_now():
    """Fresh timestamp iterator per test — each test's call count differs
    (knowledge_base on/off, refusal vs. draft, number of approval decisions),
    so a module-shared iterator would starve later tests."""
    ts = iter(f"2026-08-15T00:{i:02d}:00Z" for i in range(60))
    return lambda: next(ts)


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


@pytest.fixture
def evidence_audit_log():
    store = build_evidence_log()
    yield store
    store.close()


@pytest.fixture
def knowledge_base():
    kb = KnowledgeBase()
    kb.ingest(ALL_DOCUMENTS)
    return kb


JULY_ITEM = PBCItem(
    item_id="PBC-1",
    description="Provide the July 2026 bank reconciliation with supporting evidence.",
    period_start=date(2026, 7, 1),
    period_end=date(2026, 7, 31),
    evidence_type="bank_reconciliation",
    source_system="sample_co",
)

SEPTEMBER_ITEM = PBCItem(
    item_id="PBC-2",
    description="Provide the September 2026 bank reconciliation with supporting evidence.",
    period_start=date(2026, 9, 1),
    period_end=date(2026, 9, 30),
    evidence_type="bank_reconciliation",
    source_system="sample_co",
)


def test_end_to_end_matched_evidence_flows_to_approved(
    audit_log, approval_queue, evidence_audit_log, knowledge_base, fixed_now,
):
    client = text_response(
        "July 2026 is fully reconciled and approved "
        "(reconciliation-agent audit event 1, audit event 3)."
    )

    run = respond_to_pbc_item(
        pbc_item=JULY_ITEM,
        evidence_audit_log=evidence_audit_log,
        audit_log=audit_log,
        approval_queue=approval_queue,
        knowledge_base=knowledge_base,
        client=client,
        now=fixed_now,
    )

    assert run.tie_out.found is True
    assert run.draft.refused is False

    request = run.approval_request
    assert request.status == "pending"
    assert request.current_stage == Role.REVIEWER.value
    assert request.preparer == "audit-readiness-agent"

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

    events = audit_log.get_all()
    actions = [e.action for e in events]
    assert actions == [
        "evidence_tied_out",
        "chunks_retrieved",
        "response_drafted",
        "approval_submitted:pbc_response",
        "approval_reviewer_approve:pbc_response",
        "approval_approver_approve:pbc_response",
    ]

    tie_out_event = events[0]
    assert tie_out_event.output["found"] is True
    assert tie_out_event.output["entries"][0]["approval_status"] == "approved"

    assert audit_log.verify_chain().ok is True
    assert evidence_audit_log.verify_chain().ok is True


def test_end_to_end_evidence_gap_still_reaches_approval(
    audit_log, approval_queue, evidence_audit_log, fixed_now,
):
    client = text_response(
        "No evidence was found for the September 2026 bank reconciliation. "
        "This is an open item requiring follow-up."
    )

    run = respond_to_pbc_item(
        pbc_item=SEPTEMBER_ITEM,
        evidence_audit_log=evidence_audit_log,
        audit_log=audit_log,
        approval_queue=approval_queue,
        client=client,
        now=fixed_now,
    )

    assert run.tie_out.found is False
    assert run.draft.refused is False
    assert run.approval_request is not None
    assert run.approval_request.status == "pending"

    actions = [e.action for e in audit_log.get_all()]
    assert actions == ["evidence_tied_out", "response_drafted", "approval_submitted:pbc_response"]


def test_end_to_end_refusal_submits_nothing_for_approval(
    audit_log, approval_queue, evidence_audit_log, fixed_now,
):
    client = refusal_response(category="cyber")

    run = respond_to_pbc_item(
        pbc_item=JULY_ITEM,
        evidence_audit_log=evidence_audit_log,
        audit_log=audit_log,
        approval_queue=approval_queue,
        client=client,
        now=fixed_now,
    )

    assert run.draft.refused is True
    assert run.approval_request is None

    actions = [e.action for e in audit_log.get_all()]
    assert actions == ["evidence_tied_out", "response_refused"]
    assert approval_queue.list_pending() == []
