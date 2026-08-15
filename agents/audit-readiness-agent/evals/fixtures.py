"""Synthetic evidence fixtures for audit-readiness-agent evals.

`build_evidence_log` hand-writes a reconciliation-agent-shaped audit trail
rather than importing and running the real `reconciliation_agent` package:
CLAUDE.md rule #5 (one agent per folder) means this agent shouldn't reach
into another agent's folder, and this repo's per-agent conftest.py sys.path
pattern doesn't even put `reconciliation_agent` on the path from here. The
event shapes below are copied from `reconciliation_agent/runner.py` and
`platform/approvals/approvals/queue.py` so they match a real run exactly,
pinned independently of those modules changing later.

`BANK_REC_POLICY` is clearly-labeled synthetic placeholder text, not a real
company policy — same convention as
technical-accounting-agent/evals/fixtures.py.
"""

from audit_log import AuditEvent, AuditLogStore
from knowledge import Document

EVIDENCE_SOURCE_SYSTEM = "sample_co"
EVIDENCE_PERIOD_START = "2026-07-01"
EVIDENCE_PERIOD_END = "2026-07-31"


def build_evidence_log(db_path: str = ":memory:") -> AuditLogStore:
    """A completed, fully-approved reconciliation-agent run for July 2026 /
    sample_co: 2 exact matches, 1 tolerance match, 1 bank exception, 1 ledger
    exception — the same shape reconciliation-agent's own eval fixtures use."""
    store = AuditLogStore(db_path)

    store.append(AuditEvent(
        timestamp="2026-08-01T00:00:00Z",
        agent="reconciliation-agent",
        action="transactions_retrieved",
        actor="reconciliation-agent",
        inputs={
            "source_system": EVIDENCE_SOURCE_SYSTEM,
            "bank_folder": "sample_data/bank",
            "ledger_folder": "sample_data/ledger",
            "start_date": EVIDENCE_PERIOD_START,
            "end_date": EVIDENCE_PERIOD_END,
        },
        output={"bank_count": 4, "ledger_count": 4},
    ))
    store.append(AuditEvent(
        timestamp="2026-08-01T00:01:00Z",
        agent="reconciliation-agent",
        action="matching_completed",
        actor="reconciliation-agent",
        inputs={"tolerance_days": 2},
        output={"matched_count": 3, "exact_count": 2, "tolerance_count": 1, "exception_count": 2},
    ))
    store.append(AuditEvent(
        timestamp="2026-08-01T00:02:00Z",
        agent="reconciliation-agent",
        action="report_generated",
        actor="reconciliation-agent",
        inputs={},
        output={
            "matched_exact_count": 2,
            "matched_tolerance_count": 1,
            "bank_exception_count": 1,
            "ledger_exception_count": 1,
            "bank_total": "2575.00",
            "ledger_total": "1250.00",
            "difference": "1325.00",
        },
    ))
    store.append(AuditEvent(
        timestamp="2026-08-01T00:03:00Z",
        agent="reconciliation-agent",
        action="approval_submitted:reconciliation_report",
        actor="reconciliation-agent",
        inputs={"request_id": 1},
        output={"status": "pending", "current_stage": "reviewer"},
        approval_status="pending",
    ))
    store.append(AuditEvent(
        timestamp="2026-08-01T00:04:00Z",
        agent="reconciliation-agent",
        action="approval_reviewer_approve:reconciliation_report",
        actor="alice",
        inputs={"request_id": 1, "role": "reviewer", "decision": "approve"},
        output={"status": "pending", "current_stage": "approver"},
        approval_status="pending",
    ))
    store.append(AuditEvent(
        timestamp="2026-08-01T00:05:00Z",
        agent="reconciliation-agent",
        action="approval_approver_approve:reconciliation_report",
        actor="bob",
        inputs={"request_id": 1, "role": "approver", "decision": "approve"},
        output={"status": "approved", "current_stage": None},
        approver="bob",
        approval_status="approved",
    ))

    return store


BANK_REC_POLICY = Document(
    doc_id="policy-bank-rec-2026",
    title="Bank Reconciliation Policy",
    corpus="policy",
    text=(
        "SYNTHETIC PLACEHOLDER TEXT — not a real company policy.\n\n"
        "All bank accounts must be reconciled to the general ledger monthly. "
        "A completed reconciliation includes matched transactions, an itemized "
        "list of exceptions with explanations, and sign-off from both a "
        "reviewer and an approver before the period is considered closed. "
        "Reconciliations and their supporting evidence must be retained and "
        "made available to external auditors upon request."
    ),
)

ALL_DOCUMENTS = [BANK_REC_POLICY]
