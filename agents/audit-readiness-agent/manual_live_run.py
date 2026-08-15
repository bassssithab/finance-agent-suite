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


def main() -> None:
    evidence_audit_log = build_evidence_log()

    kb = KnowledgeBase()
    kb.ingest(ALL_DOCUMENTS)

    audit_log = AuditLogStore(":memory:")
    approval_queue = ApprovalQueue(":memory:", audit_log)
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment

    print(f"PBC item: {PBC_ITEM.item_id} — {PBC_ITEM.description}\n")

    run = respond_to_pbc_item(
        pbc_item=PBC_ITEM,
        evidence_audit_log=evidence_audit_log,
        audit_log=audit_log,
        approval_queue=approval_queue,
        knowledge_base=kb,
        client=client,
    )

    print(f"Tie-out found: {run.tie_out.found}")
    for entry in run.tie_out.entries:
        print(f"  cites audit events {entry.audit_event_ids}, approval_status={entry.approval_status}")
        print(f"  summary: {entry.summary}")

    if run.draft.refused:
        print(f"\nModel refused (category={run.draft.refusal_category!r}) — no approval request submitted.")
    else:
        print(f"\nModel: {run.draft.model}")
        print(f"Drafted response:\n{run.draft.response_text}\n")
        print(f"Citations: {run.draft.citations}")
        print(
            f"\nSubmitted for approval: request id={run.approval_request.id}, "
            f"status={run.approval_request.status}, stage={run.approval_request.current_stage}"
        )

    print(f"\nAgent audit chain verified: {audit_log.verify_chain().ok}")
    print(f"Evidence audit chain verified: {evidence_audit_log.verify_chain().ok}")

    audit_log.close()
    approval_queue.close()
    evidence_audit_log.close()


if __name__ == "__main__":
    main()
