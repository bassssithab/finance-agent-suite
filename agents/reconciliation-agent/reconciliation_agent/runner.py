"""Orchestrates one reconciliation run: connector -> matching -> report -> approvals.

Every step writes to the audit log, including the raw transactions retrieved
from the connector, so the run can be reconstructed as auditor evidence
(CLAUDE.md golden rule #3). The agent never touches the bank/ERP source
directly (rule #1) and never has an external effect without going through
platform/approvals (rule #2) — this function only ever produces a draft
report for human review.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

from approvals import ApprovalQueue, ApprovalRequest
from audit_log import AuditEvent, AuditLogStore
from connectors import FileConnector

from .matching import DEFAULT_TOLERANCE_DAYS, match_transactions
from .models import ReconciliationReport, serialize_transaction

AGENT_NAME = "reconciliation-agent"


def _default_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ReconciliationRun:
    report: ReconciliationReport
    approval_request: ApprovalRequest


def run_reconciliation(
    *,
    source_system: str,
    bank_folder: Union[str, Path],
    ledger_folder: Union[str, Path],
    audit_log: AuditLogStore,
    approval_queue: ApprovalQueue,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    tolerance_days: int = DEFAULT_TOLERANCE_DAYS,
    preparer: str = AGENT_NAME,
    now: Callable[[], str] = _default_now,
) -> ReconciliationRun:
    connector = FileConnector(
        source_system=source_system, bank_folder=bank_folder, ledger_folder=ledger_folder,
    )
    transactions = connector.fetch_transactions(start_date=start_date, end_date=end_date)
    bank = [t for t in transactions if t.source_capability == "bank"]
    ledger = [t for t in transactions if t.source_capability == "erp"]

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="transactions_retrieved",
        actor=preparer,
        inputs={
            "source_system": source_system,
            "bank_folder": str(bank_folder),
            "ledger_folder": str(ledger_folder),
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
        },
        output={
            "bank_count": len(bank),
            "ledger_count": len(ledger),
            "bank_transactions": [serialize_transaction(t) for t in bank],
            "ledger_transactions": [serialize_transaction(t) for t in ledger],
        },
    ))

    match_result = match_transactions(bank, ledger, tolerance_days=tolerance_days)

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="matching_completed",
        actor=preparer,
        inputs={"tolerance_days": tolerance_days},
        output={
            "matched_count": len(match_result.matched),
            "exact_count": sum(1 for p in match_result.matched if p.match_type == "exact"),
            "tolerance_count": sum(1 for p in match_result.matched if p.match_type == "tolerance"),
            "exception_count": len(match_result.exceptions),
        },
    ))

    report = ReconciliationReport(
        source_system=source_system,
        generated_at=now(),
        start_date=start_date,
        end_date=end_date,
        matched=match_result.matched,
        exceptions=match_result.exceptions,
    )
    report_dict = report.to_dict()

    audit_log.append(AuditEvent(
        timestamp=report.generated_at,
        agent=AGENT_NAME,
        action="report_generated",
        actor=preparer,
        inputs={},
        output=report_dict["summary"],
    ))

    approval_request = approval_queue.submit(
        agent=AGENT_NAME,
        action="reconciliation_report",
        payload=report_dict,
        preparer=preparer,
        timestamp=now(),
    )

    return ReconciliationRun(report=report, approval_request=approval_request)
