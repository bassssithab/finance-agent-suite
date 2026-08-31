"""Manual, real end-to-end check — makes real Claude API calls (vision + coding).

Not part of the automated eval suite: it lives outside evals/ and its filename
doesn't match pytest's discovery pattern, so it is never collected or run in
CI. Run it yourself to see the agent work live against a real model:

    ANTHROPIC_API_KEY=sk-... python agents/ap-agent/manual_live_run.py

Uses throwaway in-memory audit-log / approval-queue stores (nothing persisted),
the committed synthetic invoice fixtures (evals/fixtures/invoices/*.png — all
fictional, see evals/generate_sample_invoices.py), and the synthetic chart of
accounts from evals/fixtures.py. Processes all three sample invoices: the
clean one (totals tie, GL coding suggested), the services one, and the
mismatched one (the deterministic check flags the bad grand total).
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
    _PLATFORM / "connectors",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import anthropic  # noqa: E402
from approvals import ApprovalQueue  # noqa: E402
from audit_log import AuditLogStore  # noqa: E402
from connectors import FileDocumentConnector  # noqa: E402
from knowledge import KnowledgeBase  # noqa: E402

from ap_agent import process_invoice  # noqa: E402
from fixtures import ALL_DOCUMENTS  # noqa: E402

INVOICE_DIR = _ROOT / "evals" / "fixtures" / "invoices"
DOCUMENTS = [
    "clean_office_supplies.png",
    "consulting_services.png",
    "mismatched_totals.png",
]
WIDTH = 72


def header(title: str) -> None:
    print("\n" + "=" * WIDTH)
    print(f" {title}")
    print("=" * WIDTH)


def main() -> None:
    print("=" * WIDTH)
    print(" LedgerMind — AP Agent Demo")
    print(" Vision extraction + deterministic totals check + cited GL coding,")
    print(" every step gated on human approval.")
    print("=" * WIDTH)

    kb = KnowledgeBase()
    kb.ingest(ALL_DOCUMENTS)
    audit_log = AuditLogStore(":memory:")
    approval_queue = ApprovalQueue(":memory:", audit_log)
    connector = FileDocumentConnector(source_system="demo_co", folder=INVOICE_DIR)
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment

    for document_id in DOCUMENTS:
        header(f"Invoice: {document_id}")
        run = process_invoice(
            document_id=document_id,
            document_connector=connector,
            knowledge_base=kb,
            audit_log=audit_log,
            approval_queue=approval_queue,
            client=client,
        )

        if run.draft is None:
            print(f"Extraction failed (refused={run.extraction.refused}, "
                  f"category={run.extraction.refusal_category!r}) — nothing submitted.")
            continue

        inv = run.draft.invoice
        print(f"  Vendor:        {inv.vendor_name}")
        print(f"  Invoice no:    {inv.invoice_number}   Date: {inv.invoice_date}   {inv.currency}")
        print(f"  Confidence:    {inv.extraction_confidence:.2f}")
        print("  Line items:")
        for li in inv.line_items:
            print(f"    - {li.description}: {li.quantity} x {li.unit_price} = {li.line_total}")
        print(f"  Grand total:   {inv.grand_total}")

        sc = run.draft.sanity_check
        verdict = "OK — totals tie" if sc.ok else f"DISCREPANCY — line sum {sc.computed_line_sum} vs stated {sc.stated_grand_total} (diff {sc.difference})"
        print(f"  Sanity check:  {verdict}")
        for issue in sc.line_total_issues:
            print(f"    line {issue.line_index}: {issue.quantity} x {issue.unit_price} = "
                  f"{issue.computed_line_total}, invoice says {issue.stated_line_total}")

        if run.draft.coding_skipped_reason:
            print(f"  GL coding:     skipped ({run.draft.coding_skipped_reason})")
        else:
            print("  GL coding (draft):")
            for s in run.draft.gl_suggestions:
                acct = f"{s.account_code} {s.account_name}" if s.account_code else "(no account — not in chart)"
                print(f"    line {s.line_index}: {acct}  [{s.citation}]")

        req = run.approval_request
        print(f"  Approval:      request id={req.id}, status={req.status}, stage={req.current_stage}")

    header("Audit log chain verification")
    print(f"  verify_chain() -> ok={audit_log.verify_chain().ok}")
    print(f"  {len(audit_log.get_all())} events recorded across {len(DOCUMENTS)} invoices.")

    audit_log.close()
    approval_queue.close()
    header("Done")


if __name__ == "__main__":
    main()
