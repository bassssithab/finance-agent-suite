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


def main() -> None:
    kb = KnowledgeBase()
    kb.ingest(ALL_DOCUMENTS)

    audit_log = AuditLogStore(":memory:")
    approval_queue = ApprovalQueue(":memory:", audit_log)
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment

    print(f"Line item: {LINE_ITEM}\n")

    run = determine_vat_treatment(
        line_item=LINE_ITEM,
        knowledge_base=kb,
        audit_log=audit_log,
        approval_queue=approval_queue,
        client=client,
    )

    print("Retrieved chunks:")
    for event in audit_log.get_all():
        if event.action == "chunks_retrieved":
            for chunk in event.output["chunks"]:
                print(f"  [{chunk['score']:.3f}] {chunk['citation']}")

    if run.draft.refused:
        print(f"\nModel refused (category={run.draft.refusal_category!r}) — no approval request submitted.")
        return

    print(f"\nModel: {run.draft.model}")
    print(f"Drafted answer:\n{run.draft.answer_text}\n")
    print(f"Citations: {run.draft.citations}")
    print(
        f"\nSubmitted for approval: request id={run.approval_request.id}, "
        f"status={run.approval_request.status}, stage={run.approval_request.current_stage}"
    )
    print(f"Audit chain verified: {audit_log.verify_chain().ok}")

    audit_log.close()
    approval_queue.close()


if __name__ == "__main__":
    main()
