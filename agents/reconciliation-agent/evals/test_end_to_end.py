from datetime import date
from pathlib import Path

import pytest
from approvals import ApprovalQueue, Decision, Role
from audit_log import AuditLogStore
from reconciliation_agent import run_reconciliation

FIXTURES = Path(__file__).parent / "fixtures"
BANK_FOLDER = FIXTURES / "bank"
LEDGER_FOLDER = FIXTURES / "ledger"

TS = iter(f"2026-08-01T00:0{i}:00Z" for i in range(10))


def fixed_now():
    return next(TS)


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


def test_end_to_end_reconciliation_flows_to_approved(audit_log, approval_queue):
    run = run_reconciliation(
        source_system="sample_co",
        bank_folder=BANK_FOLDER,
        ledger_folder=LEDGER_FOLDER,
        audit_log=audit_log,
        approval_queue=approval_queue,
        now=fixed_now,
    )

    # Matching results, per evals/fixtures: 2 exact (REF001, REF002), 1
    # tolerance (wire transfer, 2-day gap), 1 bank exception, 1 ledger exception.
    assert len(run.report.matched) == 3
    assert sum(1 for p in run.report.matched if p.match_type == "exact") == 2
    assert sum(1 for p in run.report.matched if p.match_type == "tolerance") == 1
    assert len(run.report.exceptions) == 2

    request = run.approval_request
    assert request.status == "pending"
    assert request.current_stage == Role.REVIEWER.value
    assert request.preparer == "reconciliation-agent"
    assert request.payload["summary"]["matched_exact_count"] == 2

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

    # Full audit trail: retrieval, matching, report, submission, and both decisions.
    events = audit_log.get_all()
    actions = [e.action for e in events]
    assert actions == [
        "transactions_retrieved",
        "matching_completed",
        "report_generated",
        "approval_submitted:reconciliation_report",
        "approval_reviewer_approve:reconciliation_report",
        "approval_approver_approve:reconciliation_report",
    ]

    retrieved_event = events[0]
    assert retrieved_event.output["bank_count"] == 4
    assert retrieved_event.output["ledger_count"] == 4
    assert any(t["reference"] == "REF001" for t in retrieved_event.output["bank_transactions"])

    assert audit_log.verify_chain().ok is True


def test_end_to_end_respects_date_range_filtering(audit_log, approval_queue):
    run = run_reconciliation(
        source_system="sample_co",
        bank_folder=BANK_FOLDER,
        ledger_folder=LEDGER_FOLDER,
        audit_log=audit_log,
        approval_queue=approval_queue,
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 15),
        now=fixed_now,
    )

    # Only the two REF001/REF002 exact matches fall inside this window; the
    # ledger-only bank-fee line (2026-07-10, LREF101) has no bank counterpart
    # in range and surfaces as a ledger exception.
    assert len(run.report.matched) == 2
    assert all(p.match_type == "exact" for p in run.report.matched)
    assert len(run.report.exceptions) == 1
    assert run.report.exceptions[0].side == "ledger"
    assert run.report.exceptions[0].transaction.reference == "LREF101"
