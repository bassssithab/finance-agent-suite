"""Manual, real end-to-end check — makes one actual Claude API call.

Not part of the automated eval suite: it lives outside evals/ and its
filename doesn't match pytest's test_*.py / *_test.py discovery pattern, so
it is never collected or run in CI. Run it yourself to see the agent work
live, end to end, against a real model:

    ANTHROPIC_API_KEY=sk-... python agents/vat-treatment-agent/manual_live_run.py

Uses throwaway in-memory audit-log/approval-queue stores (nothing persisted)
and classifies one line item against the same synthetic fixture the eval
suite uses (evals/fixtures.py) — see that file's docstring for why the
answer is not real VAT guidance. This run uses the drop-shipment scenario
since it's the most nuanced (exempt vs. out-of-scope).
"""

import sys
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
from fixtures import ALL_DOCUMENTS  # noqa: E402
from knowledge import KnowledgeBase  # noqa: E402
from vat_treatment_agent import InvoiceLineItem, determine_vat_treatment  # noqa: E402

LINE_ITEM = InvoiceLineItem(
    goods_type="consumer electronics",
    customer_location="a country other than Larenthia",
    transaction_type="drop-shipped directly from a foreign supplier to the foreign customer",
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
    print(" LedgerMind — VAT Treatment Agent Demo")
    print(" Classifying a nuanced line item, grounded strictly in retrieved chunks.")
    print("=" * WIDTH)

    kb = KnowledgeBase()
    kb.ingest(ALL_DOCUMENTS)

    audit_log = AuditLogStore(":memory:")
    approval_queue = ApprovalQueue(":memory:", audit_log)
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment

    header("1. Line item")
    print(LINE_ITEM)
    print("\nAgent retrieves from platform/knowledge only — no free-standing")
    print("VAT knowledge is allowed to classify this.")

    run = determine_vat_treatment(
        line_item=LINE_ITEM,
        knowledge_base=kb,
        audit_log=audit_log,
        approval_queue=approval_queue,
        client=client,
    )

    header("2. Retrieved knowledge chunks")
    for event in audit_log.get_all():
        if event.action == "chunks_retrieved":
            for chunk in event.output["chunks"]:
                print(f"  [{chunk['score']:.3f}] {chunk['citation']}")

    header("3. Drafting via Claude API")
    if run.draft.refused:
        print(f"Model refused (category={run.draft.refusal_category!r}) — no approval request submitted.")
        header("Done")
        return

    step(f"Model: {run.draft.model}")
    print(f"{run.draft.answer_text}\n")
    print(f"Citations: {run.draft.citations}")

    header("4. Submitting for human approval")
    print(
        f"Request id={run.approval_request.id}, status={run.approval_request.status}, "
        f"stage={run.approval_request.current_stage}"
    )
    print("Drafted only — never treated as final until a reviewer and approver sign off.")

    header("5. Audit log chain verification")
    print(f"verify_chain() -> ok={audit_log.verify_chain().ok}")

    audit_log.close()
    approval_queue.close()

    header("Done")
    print("Retrieval, the drafted classification, and the approval submission are")
    print("all sitting in the hash-chained audit log as evidence.")
    print()


if __name__ == "__main__":
    main()
