"""Manual, real end-to-end check — makes real Claude API calls (vision + optional
explanation).

Not part of the automated eval suite: it lives outside evals/ and its filename
doesn't match pytest's discovery pattern, so it is never collected or run in CI.
Run it yourself to see the agent work live against a real model:

    ANTHROPIC_API_KEY=sk-... python agents/expense-agent/manual_live_run.py

Uses throwaway in-memory audit-log / approval-queue stores (nothing persisted),
the committed synthetic receipt fixtures (evals/fixtures/receipts/*.png — all
fictional, see evals/generate_sample_receipts.py), and the synthetic
expense-policy corpus from evals/fixtures.py. Checks all three sample receipts
against a policy with a $75 meal cap, a $60 taxi cap, a $250 lodging cap and a
90-day receipt-age limit, aged against evals/fixtures.py AS_OF.
"""

import sys
from decimal import Decimal
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_PLATFORM = _ROOT.parent.parent / "platform"

for path in (
    _ROOT,
    _ROOT / "evals",
    _PLATFORM / "knowledge",
    _PLATFORM / "approvals",
    _PLATFORM / "audit-log",
    _PLATFORM / "connectors",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import anthropic  # noqa: E402
from approvals import ApprovalQueue  # noqa: E402
from audit_log import AuditLogStore  # noqa: E402
from connectors import FileDocumentConnector  # noqa: E402
from knowledge import KnowledgeBase  # noqa: E402

from expense_agent import ExpensePolicy, check_receipt_policy_compliance  # noqa: E402
from fixtures import ALL_DOCUMENTS, AS_OF  # noqa: E402

RECEIPT_DIR = _ROOT / "evals" / "fixtures" / "receipts"
DOCUMENTS = ["compliant_taxi.png", "over_limit_dinner.png", "stale_hotel.png"]
WIDTH = 72

POLICY = ExpensePolicy(
    category_limits={
        "meals": Decimal("75.00"),
        "travel - taxi": Decimal("60.00"),
        "lodging": Decimal("250.00"),
    },
    max_receipt_age_days=90,
)


def header(title: str) -> None:
    print("\n" + "=" * WIDTH)
    print(f" {title}")
    print("=" * WIDTH)


def main() -> None:
    print("=" * WIDTH)
    print(" LedgerMind — Expense Agent Demo")
    print(" Vision extraction + deterministic policy compliance check + cited")
    print(f" policy explanations, gated on human approval. As of {AS_OF.isoformat()}.")
    print("=" * WIDTH)

    kb = KnowledgeBase()
    kb.ingest(ALL_DOCUMENTS)
    audit_log = AuditLogStore(":memory:")
    approval_queue = ApprovalQueue(":memory:", audit_log)
    connector = FileDocumentConnector(source_system="demo_co", folder=RECEIPT_DIR)
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment

    for document_id in DOCUMENTS:
        header(f"Receipt: {document_id}")
        run = check_receipt_policy_compliance(
            document_id=document_id,
            document_connector=connector,
            knowledge_base=kb,
            audit_log=audit_log,
            approval_queue=approval_queue,
            policy=POLICY,
            as_of_date=AS_OF,
            client=client,
        )

        if run.draft is None:
            print(f"Extraction failed (refused={run.extraction.refused}, "
                  f"category={run.extraction.refusal_category!r}) — nothing submitted.")
            continue

        r = run.draft.receipt
        print(f"  Vendor:     {r.vendor}")
        print(f"  Date:       {r.date}    {r.currency} {r.amount}")
        print(f"  Category:   {r.expense_category}   (confidence {r.extraction_confidence:.2f})")

        c = run.draft.compliance
        verdict = "PASS — within policy" if c.passed else "FLAGGED"
        print(f"  Compliance: {verdict}")
        for v in c.violations:
            print(f"    · {v.code} ({v.field}): {v.detail}")

        if run.draft.explanation_skipped_reason:
            print(f"  Explanation: skipped ({run.draft.explanation_skipped_reason})")
        else:
            for e in run.draft.explanations:
                print(f"  Explanation [{e.code}]: {e.explanation}")
                for citation in e.citations:
                    print(f"    [{citation}]")

        req = run.approval_request
        print(f"  Approval:   request id={req.id}, status={req.status}, stage={req.current_stage}")

    header("Audit log chain verification")
    print(f"  verify_chain() -> ok={audit_log.verify_chain().ok}")
    print(f"  {len(audit_log.get_all())} events recorded across {len(DOCUMENTS)} receipts.")

    audit_log.close()
    approval_queue.close()
    header("Done")


if __name__ == "__main__":
    main()
