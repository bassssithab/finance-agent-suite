#!/usr/bin/env python3
"""Demo: bank/ledger reconciliation, end to end, narrated in the terminal.

Runs the reconciliation-agent's real `run_reconciliation` against the sample
CSVs in `sample_data/`, then walks the resulting report, approval chain, and
audit log the same way a human would trace it. Everything here is plain
deterministic code (CLAUDE.md rule #4) calling existing platform/agent
modules unmodified — no LLM calls, stdlib only.

Run: python demo.py
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLATFORM = ROOT / "platform"
for path in (
    ROOT / "agents" / "reconciliation-agent",
    PLATFORM / "connectors",
    PLATFORM / "approvals",
    PLATFORM / "audit-log",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from approvals import ApprovalQueue, Decision, Role  # noqa: E402
from audit_log import AuditLogStore  # noqa: E402
from reconciliation_agent import run_reconciliation  # noqa: E402

SAMPLE_DATA = ROOT / "sample_data"
BANK_FOLDER = SAMPLE_DATA / "bank"
LEDGER_FOLDER = SAMPLE_DATA / "ledger"

WIDTH = 72


def header(title: str) -> None:
    print()
    print("=" * WIDTH)
    print(f" {title}")
    print("=" * WIDTH)


def step(text: str) -> None:
    print(f"\n--- {text} ---")


def money(amount) -> str:
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.2f}"


def main() -> None:
    print("=" * WIDTH)
    print(" LedgerMind — Reconciliation Agent Demo")
    print(" A bank statement and a general ledger walk into an audit...")
    print("=" * WIDTH)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        audit_log = AuditLogStore(tmp_path / "audit.db")
        approval_queue = ApprovalQueue(tmp_path / "approvals.db", audit_log)

        header("1. Fetching transactions")
        print(f"Source system : sample_co")
        print(f"Bank folder   : {BANK_FOLDER.relative_to(ROOT)}")
        print(f"Ledger folder : {LEDGER_FOLDER.relative_to(ROOT)}")
        print("\nAgent calls platform/connectors (never touches the bank/ERP directly)...")

        run = run_reconciliation(
            source_system="sample_co",
            bank_folder=BANK_FOLDER,
            ledger_folder=LEDGER_FOLDER,
            audit_log=audit_log,
            approval_queue=approval_queue,
        )
        report = run.report

        retrieved_event = audit_log.get_all()[0]
        bank_count = retrieved_event.output["bank_count"]
        ledger_count = retrieved_event.output["ledger_count"]
        print(f"\nFetched {bank_count} bank transactions and {ledger_count} ledger transactions.")

        header("2. Matching bank against ledger")
        exact = [p for p in report.matched if p.match_type == "exact"]
        tolerance = [p for p in report.matched if p.match_type == "tolerance"]

        step(f"Exact matches ({len(exact)})")
        for pair in exact:
            print(
                f"  bank {pair.bank.reference!r} {money(pair.bank.amount)} on {pair.bank.date} "
                f"== ledger {pair.ledger.reference!r} {money(pair.ledger.amount)} on {pair.ledger.date}"
            )

        step(f"Tolerance matches ({len(tolerance)})")
        for pair in tolerance:
            print(
                f"  bank {money(pair.bank.amount)} on {pair.bank.date} "
                f"~= ledger {money(pair.ledger.amount)} on {pair.ledger.date} "
                f"(date delta: {pair.date_delta_days} day(s))"
            )

        header("3. Exceptions flagged")
        if not report.exceptions:
            print("  None — every transaction matched.")
        for exc in report.exceptions:
            t = exc.transaction
            print(
                f"  [{exc.side}] {money(t.amount)} on {t.date} "
                f"(ref: {t.reference or 'none'}) — {exc.reason}"
            )

        header("4. Submitting report for approval")
        request = run.approval_request
        print(f"Request #{request.id} submitted by {request.preparer!r}")
        print(f"Status: {request.status} | awaiting: {request.current_stage}")
        print(
            f"Summary: {len(exact)} exact, {len(tolerance)} tolerance, "
            f"{len(report.exceptions)} exception(s), difference {money(report.difference)}"
        )

        header("5. Human review chain")
        step("Reviewer: alice")
        reviewed = approval_queue.decide(
            request.id, actor="alice", role=Role.REVIEWER, decision=Decision.APPROVE,
            timestamp="2026-08-02T00:00:00Z", comment="Matches look right, exceptions are explainable.",
        )
        print(f"  alice approves -> status: {reviewed.status}, next stage: {reviewed.current_stage}")

        step("Approver: bob")
        approved = approval_queue.decide(
            request.id, actor="bob", role=Role.APPROVER, decision=Decision.APPROVE,
            timestamp="2026-08-02T00:01:00Z", comment="Approved for close.",
        )
        print(f"  bob approves -> status: {approved.status}")

        header("6. Audit log chain verification")
        events = audit_log.get_all()
        print("Recorded events, in order:")
        for e in events:
            print(f"  - {e.action} (actor: {e.actor})")

        verification = audit_log.verify_chain()
        print(f"\nverify_chain() -> ok={verification.ok}", end="")
        if not verification.ok:
            print(f", broken_record_id={verification.broken_record_id}, reason={verification.reason}")
        else:
            print(" (hash chain intact, no tampering detected)")

        audit_log.close()
        approval_queue.close()

    header("Done")
    print("Every step above — retrieval, matching, the draft report, and both")
    print("approvals — is sitting in the hash-chained audit log as evidence.")
    print()


if __name__ == "__main__":
    main()
