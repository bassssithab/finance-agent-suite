import pytest

from approvals import (
    ApprovalQueue,
    Decision,
    Role,
    SegregationOfDutyError,
    WrongStageError,
)
from audit_log import AuditLogStore

TS = "2026-08-01T12:00:00Z"


@pytest.fixture
def audit_log(tmp_path):
    store = AuditLogStore(tmp_path / "audit.db")
    yield store
    store.close()


@pytest.fixture
def queue(tmp_path, audit_log):
    q = ApprovalQueue(tmp_path / "approvals.db", audit_log)
    yield q
    q.close()


def submit_je(queue, preparer="reconciliation-agent"):
    return queue.submit(
        agent="reconciliation-agent",
        action="draft_journal_entry",
        payload={"debit": "1000", "credit": "2000", "amount": 500},
        preparer=preparer,
        timestamp=TS,
    )


def test_submit_creates_pending_request_and_audit_event(queue, audit_log):
    request = submit_je(queue)

    assert request.status == "pending"
    assert request.current_stage == Role.REVIEWER.value

    events = audit_log.get_all()
    assert len(events) == 1
    assert events[0].approval_status == "pending"
    assert events[0].inputs["request_id"] == request.id


def test_full_approval_flow_reviewer_then_approver(queue, audit_log):
    request = submit_je(queue, preparer="reconciliation-agent")

    reviewed = queue.decide(
        request.id, actor="bob", role=Role.REVIEWER, decision=Decision.APPROVE, timestamp=TS,
    )
    assert reviewed.status == "pending"
    assert reviewed.current_stage == Role.APPROVER.value

    approved = queue.decide(
        reviewed.id, actor="carol", role=Role.APPROVER, decision=Decision.APPROVE, timestamp=TS,
    )
    assert approved.status == "approved"
    assert approved.current_stage is None

    assert len(audit_log.get_all()) == 3
    assert audit_log.verify_chain().ok is True


def test_reject_at_reviewer_stage_is_terminal(queue):
    request = submit_je(queue)

    rejected = queue.decide(
        request.id, actor="bob", role=Role.REVIEWER, decision=Decision.REJECT,
        timestamp=TS, comment="wrong account",
    )
    assert rejected.status == "rejected"
    assert rejected.current_stage is None

    with pytest.raises(WrongStageError):
        queue.decide(
            request.id, actor="carol", role=Role.APPROVER, decision=Decision.APPROVE, timestamp=TS,
        )


def test_edit_replaces_payload_and_advances_stage(queue):
    request = submit_je(queue)

    edited = queue.decide(
        request.id, actor="bob", role=Role.REVIEWER, decision=Decision.EDIT,
        timestamp=TS, edited_payload={"debit": "1010", "credit": "2000", "amount": 500},
    )

    assert edited.current_stage == Role.APPROVER.value
    assert edited.payload == {"debit": "1010", "credit": "2000", "amount": 500}

    history = queue.decisions_for(request.id)
    assert history[0].decision == Decision.EDIT.value
    assert history[0].payload_after == {"debit": "1010", "credit": "2000", "amount": 500}


def test_segregation_of_duty_blocks_same_actor_twice(queue):
    request = submit_je(queue, preparer="alice")

    with pytest.raises(SegregationOfDutyError):
        queue.decide(
            request.id, actor="alice", role=Role.REVIEWER, decision=Decision.APPROVE, timestamp=TS,
        )

    queue.decide(request.id, actor="bob", role=Role.REVIEWER, decision=Decision.APPROVE, timestamp=TS)

    with pytest.raises(SegregationOfDutyError):
        queue.decide(
            request.id, actor="bob", role=Role.APPROVER, decision=Decision.APPROVE, timestamp=TS,
        )


def test_wrong_stage_role_rejected(queue):
    request = submit_je(queue)

    with pytest.raises(WrongStageError):
        queue.decide(
            request.id, actor="carol", role=Role.APPROVER, decision=Decision.APPROVE, timestamp=TS,
        )


def test_list_pending_filters_by_role(queue):
    r1 = submit_je(queue)
    r2 = submit_je(queue)
    queue.decide(r2.id, actor="bob", role=Role.REVIEWER, decision=Decision.APPROVE, timestamp=TS)

    awaiting_review = queue.list_pending(role=Role.REVIEWER)
    awaiting_approval = queue.list_pending(role=Role.APPROVER)

    assert [r.id for r in awaiting_review] == [r1.id]
    assert [r.id for r in awaiting_approval] == [r2.id]
    assert len(queue.list_pending()) == 2
