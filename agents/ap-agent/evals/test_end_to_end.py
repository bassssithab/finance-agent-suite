from decimal import Decimal
from pathlib import Path

import pytest
from approvals import ApprovalQueue, Decision, Role
from audit_log import AuditLogStore
from connectors import FileDocumentConnector
from knowledge import KnowledgeBase

from ap_agent import process_invoice
from fakes import invoice_client, refusal_client
from fixtures import ALL_DOCUMENTS, CODING_PAYLOADS, COA_CITATION, record_invoice_payload

INVOICE_DIR = Path(__file__).parent / "fixtures" / "invoices"

TS = iter(f"2026-08-31T00:{i // 60:02d}:{i % 60:02d}Z" for i in range(60))


def fixed_now():
    return next(TS)


@pytest.fixture
def document_connector():
    return FileDocumentConnector(source_system="sample_co", folder=INVOICE_DIR)


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


def _client(slug):
    return invoice_client(
        record_invoice_payload(slug), coding_payload=CODING_PAYLOADS[slug]
    )


def test_clean_invoice_flows_to_approved_with_gl_coding(
    document_connector, knowledge_base, audit_log, approval_queue
):
    run = process_invoice(
        document_id="clean_office_supplies.png",
        document_connector=document_connector,
        knowledge_base=knowledge_base,
        audit_log=audit_log,
        approval_queue=approval_queue,
        client=_client("clean_office_supplies"),
        now=fixed_now,
    )

    assert run.draft is not None
    assert run.draft.discrepancy_flagged is False
    assert run.draft.sanity_check.ok is True
    assert run.draft.invoice.grand_total == Decimal("465.00")
    assert [s.account_code for s in run.draft.gl_suggestions] == ["6100", "6100", "6100"]
    assert all(s.citation == COA_CITATION for s in run.draft.gl_suggestions)
    assert run.draft.coding_skipped_reason is None

    request = run.approval_request
    assert request.status == "pending"
    assert request.current_stage == Role.REVIEWER.value
    assert request.preparer == "ap-agent"
    assert request.payload["discrepancy_flagged"] is False
    assert request.payload["source_document"]["filename"] == "clean_office_supplies.png"
    assert request.payload["invoice"]["extraction_confidence"] == 0.97

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
        "invoice_document_received",
        "invoice_extracted",
        "sanity_check_completed",
        "gl_coding_suggested",
        "approval_submitted:ap_invoice_coding",
        "approval_reviewer_approve:ap_invoice_coding",
        "approval_approver_approve:ap_invoice_coding",
    ]

    events = audit_log.get_all()
    received = events[0]
    assert received.output["sha256"] and received.output["media_type"] == "image/png"
    extracted = events[1]
    assert extracted.model == "claude-sonnet-5"
    assert extracted.prompt_hash == run.draft.extraction_prompt_hash
    assert extracted.output["extraction_confidence"] == 0.97
    sanity = events[2]
    assert sanity.output["discrepancy_flagged"] is False
    assert sanity.output["difference"] == "0.00"
    coding = events[3]
    assert coding.output["citations"] == [COA_CITATION]
    assert len(coding.output["suggestions"]) == 3

    assert audit_log.verify_chain().ok is True


def test_mismatched_invoice_is_flagged_but_still_submitted(
    document_connector, knowledge_base, audit_log, approval_queue
):
    run = process_invoice(
        document_id="mismatched_totals.png",
        document_connector=document_connector,
        knowledge_base=knowledge_base,
        audit_log=audit_log,
        approval_queue=approval_queue,
        client=_client("mismatched_totals"),
        now=fixed_now,
    )

    assert run.draft.discrepancy_flagged is True
    assert run.draft.sanity_check.difference == Decimal("81.00")
    assert run.draft.sanity_check.line_total_issues == []
    assert run.approval_request is not None
    assert run.approval_request.status == "pending"
    assert run.approval_request.payload["discrepancy_flagged"] is True
    assert run.approval_request.payload["sanity_check"]["difference"] == "81.00"

    sanity_event = next(
        e for e in audit_log.get_all() if e.action == "sanity_check_completed"
    )
    assert sanity_event.output["discrepancy_flagged"] is True
    assert sanity_event.output["computed_line_sum"] == "1690.00"
    assert audit_log.verify_chain().ok is True


def test_extraction_refusal_logs_and_submits_nothing(
    document_connector, knowledge_base, audit_log, approval_queue
):
    run = process_invoice(
        document_id="clean_office_supplies.png",
        document_connector=document_connector,
        knowledge_base=knowledge_base,
        audit_log=audit_log,
        approval_queue=approval_queue,
        client=refusal_client(category="cyber"),
        now=fixed_now,
    )

    assert run.draft is None
    assert run.approval_request is None
    assert run.extraction.refused is True

    actions = [e.action for e in audit_log.get_all()]
    assert actions == ["invoice_document_received", "invoice_extraction_failed"]
    assert approval_queue.list_pending() == []

    failed = audit_log.get_all()[1]
    assert failed.output["refused"] is True
    assert failed.output["refusal_category"] == "cyber"
    assert audit_log.verify_chain().ok is True


def test_no_chart_of_accounts_knowledge_skips_coding_but_still_submits(
    document_connector, empty_knowledge_base, audit_log, approval_queue
):
    run = process_invoice(
        document_id="consulting_services.png",
        document_connector=document_connector,
        knowledge_base=empty_knowledge_base,
        audit_log=audit_log,
        approval_queue=approval_queue,
        client=invoice_client(record_invoice_payload("consulting_services")),
        now=fixed_now,
    )

    assert run.draft is not None
    assert run.draft.gl_suggestions == []
    assert run.draft.coding_skipped_reason == "no_relevant_knowledge"
    assert run.approval_request.status == "pending"

    actions = [e.action for e in audit_log.get_all()]
    assert actions == [
        "invoice_document_received",
        "invoice_extracted",
        "sanity_check_completed",
        "gl_coding_skipped",
        "approval_submitted:ap_invoice_coding",
    ]
    skipped = next(e for e in audit_log.get_all() if e.action == "gl_coding_skipped")
    assert skipped.output["reason"] == "no_relevant_knowledge"
    assert audit_log.verify_chain().ok is True


def test_reviewer_can_reject_a_flagged_invoice(
    document_connector, knowledge_base, audit_log, approval_queue
):
    run = process_invoice(
        document_id="mismatched_totals.png",
        document_connector=document_connector,
        knowledge_base=knowledge_base,
        audit_log=audit_log,
        approval_queue=approval_queue,
        client=_client("mismatched_totals"),
        now=fixed_now,
    )

    rejected = approval_queue.decide(
        run.approval_request.id, actor="alice", role=Role.REVIEWER,
        decision=Decision.REJECT, timestamp=fixed_now(), comment="totals don't tie",
    )
    assert rejected.status == "rejected"
    assert audit_log.verify_chain().ok is True
