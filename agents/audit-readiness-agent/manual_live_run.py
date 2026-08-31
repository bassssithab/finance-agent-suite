"""Manual, real end-to-end check — makes one actual Claude API call.

Not part of the automated eval suite: it lives outside evals/ and its
filename doesn't match pytest's test_*.py / *_test.py discovery pattern, so
it is never collected or run in CI. Run it yourself to see the agent work
live, end to end, against a real model:

    ANTHROPIC_API_KEY=sk-... python agents/audit-readiness-agent/manual_live_run.py

Uses throwaway in-memory audit-log/approval-queue stores (nothing persisted)
plus the same synthetic evidence log and knowledge fixture the eval suite
uses (evals/fixtures.py — see that file's docstring for why the evidence and
policy text are not real).
"""

import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_PLATFORM = _ROOT.parent.parent / "platform"

for path in (
    _ROOT,
    _ROOT / "evals",
    _PLATFORM / "knowledge",
    _PLATFORM / "approvals",
    _PLATFORM / "audit-log",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import anthropic  # noqa: E402  (after sys.path setup)
from approvals import ApprovalQueue  # noqa: E402
from audit_log import AuditLogStore  # noqa: E402
from audit_readiness_agent import PBCItem, respond_to_pbc_item  # noqa: E402
from fixtures import ALL_DOCUMENTS, build_evidence_log  # noqa: E402
from knowledge import KnowledgeBase  # noqa: E402

PBC_ITEM = PBCItem(
    item_id="PBC-1",
    description="Provide the July 2026 bank reconciliation with supporting evidence.",
    period_start=date(2026, 7, 1),
    period_end=date(2026, 7, 31),
    evidence_type="bank_reconciliation",
    source_system="sample_co",
)

WIDTH = 72


def header(title: str) -> None:
    print()
    print("=" * WIDTH)
    print(f" {title}")
    print("=" * WIDTH)


def step(text: str) -> None:
    print(f"\n--- {text} ---")


def main() -> None:
    print("=" * WIDTH)
    print(" LedgerMind — Audit Readiness Agent Demo (flagship)")
    print(" Ties a PBC request to a completed reconciliation-agent run,")
    print(" then drafts a cited response via Claude.")
    print("=" * WIDTH)

    evidence_audit_log = build_evidence_log()

    kb = KnowledgeBase()
    kb.ingest(ALL_DOCUMENTS)

    audit_log = AuditLogStore(":memory:")
    approval_queue = ApprovalQueue(":memory:", audit_log)
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment

    header("1. PBC request")
    print(f"{PBC_ITEM.item_id} — {PBC_ITEM.description}")
    print("\nAgent tie-out is deterministic code, matched on period/evidence_type,")
    print("never on this free-text description.")

    run = respond_to_pbc_item(
        pbc_item=PBC_ITEM,
        evidence_audit_log=evidence_audit_log,
        audit_log=audit_log,
        approval_queue=approval_queue,
        knowledge_base=kb,
        client=client,
    )

    header("2. Tie-out against reconciliation-agent's audit log")
    print(f"Tie-out found: {run.tie_out.found}")
    for entry in run.tie_out.entries:
        print(f"  cites audit events {entry.audit_event_ids}, approval_status={entry.approval_status}")
        print(f"  summary: {entry.summary}")

    header("3. Drafting via Claude API")
    if run.draft.refused:
        print(f"Model refused (category={run.draft.refusal_category!r}) — no approval request submitted.")
    else:
        step(f"Model: {run.draft.model}")
        print(f"{run.draft.response_text}\n")
        print(f"Citations: {run.draft.citations}")

        header("4. Submitting for human approval")
        print(
            f"Request id={run.approval_request.id}, status={run.approval_request.status}, "
            f"stage={run.approval_request.current_stage}"
        )
        print("Drafted only — never treated as final until a reviewer and approver sign off.")

    header("5. Audit log chain verification")
    print(f"Agent audit chain verified: {audit_log.verify_chain().ok}")
    print(f"Evidence audit chain verified: {evidence_audit_log.verify_chain().ok}")

    audit_log.close()
    approval_queue.close()
    evidence_audit_log.close()

    header("Done")
    print("The tie-out, the drafted response, and the approval submission all")
    print("cite the evidence audit log's events rather than copying them —")
    print("provenance stays traceable back to the source run.")
    print()


if __name__ == "__main__":
    main()
