from decimal import Decimal
from pathlib import Path

import pytest
from approvals import ApprovalQueue, Decision, Role
from audit_log import AuditLogStore
from connectors import FileDocumentConnector
from knowledge import KnowledgeBase

from expense_agent import ExpensePolicy, check_receipt_policy_compliance
from fakes import (
    receipt_client,
    receipt_then_explanation_no_tool,
    receipt_then_explanation_refusal,
    refusal_client,
)
from fixtures import ALL_DOCUMENTS, AS_OF, POLICY_CITATION, explanations_payload, record_receipt_payload

RECEIPT_DIR = Path(__file__).parent / "fixtures" / "receipts"

TS = iter(f"2026-09-01T00:{i // 60:02d}:{i % 60:02d}Z" for i in range(120))


def fixed_now():
    return next(TS)


SAMPLE_POLICY = ExpensePolicy(
    category_limits={
        "meals": Decimal("75.00"),
        "travel - taxi": Decimal("60.00"),
        "lodging": Decimal("250.00"),
    },
    max_receipt_age_days=90,
)


@pytest.fixture
def knowledge_base():
    kb = KnowledgeBase()
    kb.ingest(ALL_DOCUMENTS)
    return kb


@pytest.fixture
def empty_knowledge_base():
    return KnowledgeBase()


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


def _run(slug, client, knowledge_base, audit_log, approval_queue):
    return check_receipt_policy_compliance(
        document_id=f"{slug}.png",
        document_connector=FileDocumentConnector(source_system="sample_co", folder=RECEIPT_DIR),
        knowledge_base=knowledge_base,
        audit_log=audit_log,
        approval_queue=approval_queue,
        policy=SAMPLE_POLICY,
        as_of_date=AS_OF,
        client=client,
        now=fixed_now,
    )


def test_compliant_receipt_flows_to_approved(
    knowledge_base, audit_log, approval_queue
):
    run = _run(
        "compliant_taxi",
        receipt_client(record_receipt_payload("compliant_taxi")),
        knowledge_base, audit_log, approval_queue,
    )

    assert run.draft is not None
    assert run.draft.compliance.passed is True
    assert run.draft.compliance_flagged is False
    assert run.draft.explanation_skipped_reason == "no_violations"
    assert run.draft.explanations == []

    request = run.approval_request
    assert request.status == "pending"
    assert request.preparer == "expense-agent"
    assert request.payload["source_document"]["filename"] == "compliant_taxi.png"
    assert request.payload["receipt"]["extraction_confidence"] == 0.96
    assert request.payload["compliance_flagged"] is False

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

    actions = [e.action for e in audit_log.get_all()]
    assert actions == [
        "receipt_document_received",
        "receipt_extracted",
        "compliance_check_completed",
        "policy_explanation_skipped",
        "approval_submitted:expense_policy_check",
        "approval_reviewer_approve:expense_policy_check",
        "approval_approver_approve:expense_policy_check",
    ]

    events = audit_log.get_all()
    assert events[0].output["media_type"] == "image/png" and events[0].output["sha256"]
    assert events[1].model == "claude-sonnet-5"
    assert events[1].prompt_hash == run.draft.extraction_prompt_hash
    assert events[1].output["extraction_confidence"] == 0.96
    assert events[2].output["compliance_flagged"] is False
    assert events[3].output["reason"] == "no_violations"
    assert audit_log.verify_chain().ok is True


def test_over_limit_receipt_is_flagged_with_a_cited_explanation(
    knowledge_base, audit_log, approval_queue
):
    run = _run(
        "over_limit_dinner",
        receipt_client(
            record_receipt_payload("over_limit_dinner"),
            explanation_payload=explanations_payload(["category_over_limit"]),
        ),
        knowledge_base, audit_log, approval_queue,
    )

    assert run.draft.compliance_flagged is True
    assert [v.code for v in run.draft.compliance.violations] == ["category_over_limit"]
    assert run.draft.explanation_skipped_reason is None
    assert [e.code for e in run.draft.explanations] == ["category_over_limit"]
    assert run.draft.explanations[0].citations == [POLICY_CITATION]
    assert run.draft.model == "claude-sonnet-5"

    assert run.approval_request.status == "pending"
    assert run.approval_request.payload["compliance"]["violations"][0]["field"] == "amount"

    actions = [e.action for e in audit_log.get_all()]
    assert actions == [
        "receipt_document_received",
        "receipt_extracted",
        "compliance_check_completed",
        "policy_explanation_drafted",
        "approval_submitted:expense_policy_check",
    ]

    completed = next(e for e in audit_log.get_all() if e.action == "compliance_check_completed")
    assert completed.output["compliance_flagged"] is True
    assert "over by" in completed.output["violations"][0]["detail"]

    drafted = next(e for e in audit_log.get_all() if e.action == "policy_explanation_drafted")
    assert drafted.prompt_hash == run.draft.explanation_prompt_hash
    assert POLICY_CITATION in drafted.output["citations"]
    assert audit_log.verify_chain().ok is True


def test_stale_receipt_is_flagged_but_still_submitted(
    knowledge_base, audit_log, approval_queue
):
    run = _run(
        "stale_hotel",
        receipt_client(
            record_receipt_payload("stale_hotel"),
            explanation_payload=explanations_payload(["receipt_too_old"]),
        ),
        knowledge_base, audit_log, approval_queue,
    )

    assert [v.code for v in run.draft.compliance.violations] == ["receipt_too_old"]
    assert run.draft.compliance_flagged is True
    assert run.approval_request.status == "pending"
    assert [e.code for e in run.draft.explanations] == ["receipt_too_old"]
    assert audit_log.verify_chain().ok is True


def test_extraction_refusal_logs_and_submits_nothing(
    knowledge_base, audit_log, approval_queue
):
    run = _run(
        "compliant_taxi", refusal_client(category="cyber"),
        knowledge_base, audit_log, approval_queue,
    )

    assert run.draft is None
    assert run.approval_request is None
    assert run.extraction.refused is True

    actions = [e.action for e in audit_log.get_all()]
    assert actions == ["receipt_document_received", "receipt_extraction_failed"]
    assert approval_queue.list_pending() == []
    assert audit_log.get_all()[1].output["refusal_category"] == "cyber"
    assert audit_log.verify_chain().ok is True


def test_violations_but_no_relevant_knowledge_skips_explanation_and_still_submits(
    empty_knowledge_base, audit_log, approval_queue
):
    run = _run(
        "over_limit_dinner",
        receipt_client(record_receipt_payload("over_limit_dinner")),
        empty_knowledge_base, audit_log, approval_queue,
    )

    assert run.draft.compliance_flagged is True
    assert run.draft.explanations == []
    assert run.draft.explanation_skipped_reason == "no_relevant_knowledge"
    assert run.draft.explanation_chunk_ids == []
    assert run.approval_request.status == "pending"

    actions = [e.action for e in audit_log.get_all()]
    assert actions == [
        "receipt_document_received",
        "receipt_extracted",
        "compliance_check_completed",
        "policy_explanation_skipped",
        "approval_submitted:expense_policy_check",
    ]
    skipped = next(e for e in audit_log.get_all() if e.action == "policy_explanation_skipped")
    assert skipped.output["reason"] == "no_relevant_knowledge"
    assert audit_log.verify_chain().ok is True


def test_explanation_refusal_still_submits_the_draft(
    knowledge_base, audit_log, approval_queue
):
    run = _run(
        "over_limit_dinner",
        receipt_then_explanation_refusal(record_receipt_payload("over_limit_dinner"), category="cyber"),
        knowledge_base, audit_log, approval_queue,
    )

    assert run.draft.compliance_flagged is True
    assert run.draft.explanations == []
    assert run.draft.explanation_skipped_reason == "explanation_refused"
    assert run.approval_request.status == "pending"

    failed = next(e for e in audit_log.get_all() if e.action == "policy_explanation_failed")
    assert failed.output["refused"] is True
    assert failed.output["refusal_category"] == "cyber"
    assert failed.prompt_hash == run.draft.explanation_prompt_hash
    assert audit_log.verify_chain().ok is True


def test_unparseable_explanation_response_still_submits_the_draft(
    knowledge_base, audit_log, approval_queue
):
    run = _run(
        "over_limit_dinner",
        receipt_then_explanation_no_tool(record_receipt_payload("over_limit_dinner")),
        knowledge_base, audit_log, approval_queue,
    )

    assert run.draft.explanation_skipped_reason == "explanation_failed"
    failed = next(e for e in audit_log.get_all() if e.action == "policy_explanation_failed")
    assert "no record_policy_explanations tool call" in failed.output["parse_error"]
    assert run.approval_request.status == "pending"
    assert audit_log.verify_chain().ok is True


def test_reviewer_can_reject_a_flagged_receipt(
    knowledge_base, audit_log, approval_queue
):
    run = _run(
        "over_limit_dinner",
        receipt_client(
            record_receipt_payload("over_limit_dinner"),
            explanation_payload=explanations_payload(["category_over_limit"]),
        ),
        knowledge_base, audit_log, approval_queue,
    )

    rejected = approval_queue.decide(
        run.approval_request.id, actor="alice", role=Role.REVIEWER,
        decision=Decision.REJECT, timestamp=fixed_now(), comment="over the meal cap, no manager sign-off attached",
    )
    assert rejected.status == "rejected"
    assert audit_log.verify_chain().ok is True
