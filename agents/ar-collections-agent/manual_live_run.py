"""Manual, real end-to-end check — makes one real Claude API call (dunning
emails).

Not part of the automated eval suite: it lives outside evals/ and its filename
doesn't match pytest's discovery pattern, so it is never collected or run in
CI. Run it yourself to see the agent work live against a real model:

    ANTHROPIC_API_KEY=sk-... python agents/ar-collections-agent/manual_live_run.py

Uses throwaway in-memory audit-log / approval-queue stores (nothing persisted),
the committed synthetic open-invoice fixture (evals/fixtures/open_invoices/
mixed_aging.csv — all fictional), and the synthetic collections-policy corpus
from evals/fixtures.py. Ages against the fixed AS_OF date so the buckets match
the eval suite.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_PLATFORM = _ROOT.parent.parent / "platform"

for path in (
    _ROOT,
    _ROOT / "evals",
    _PLATFORM / "connectors",
    _PLATFORM / "knowledge",
    _PLATFORM / "approvals",
    _PLATFORM / "audit-log",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import anthropic  # noqa: E402
from approvals import ApprovalQueue  # noqa: E402
from audit_log import AuditLogStore  # noqa: E402
from knowledge import KnowledgeBase  # noqa: E402

from ar_collections_agent import run_ar_collections_analysis  # noqa: E402
from fixtures import ALL_DOCUMENTS, AS_OF  # noqa: E402

INVOICES_DIR = _ROOT / "evals" / "fixtures" / "open_invoices"
WIDTH = 72


def header(title: str) -> None:
    print("\n" + "=" * WIDTH)
    print(f" {title}")
    print("=" * WIDTH)


def main() -> None:
    print("=" * WIDTH)
    print(" LedgerMind — AR Collections Agent Demo")
    print(" Deterministic invoice aging + dunning-flagging + tone-escalating")
    print(" cited dunning emails, gated on human approval. No email is sent.")
    print("=" * WIDTH)

    # Only the mixed_aging book, copied into its own folder (the connector reads
    # every CSV in a folder).
    import tempfile
    tmp = Path(tempfile.mkdtemp()) / "open_invoices"
    tmp.mkdir()
    (tmp / "mixed_aging.csv").write_text((INVOICES_DIR / "mixed_aging.csv").read_text())

    kb = KnowledgeBase()
    kb.ingest(ALL_DOCUMENTS)
    audit_log = AuditLogStore(":memory:")
    approval_queue = ApprovalQueue(":memory:", audit_log)
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment

    run = run_ar_collections_analysis(
        source_system="demo_co",
        invoices_folder=tmp,
        knowledge_base=kb,
        audit_log=audit_log,
        approval_queue=approval_queue,
        as_of_date=AS_OF,
        client=client,
    )
    report = run.report

    header(f"Aging — as of {report.as_of_date} ({report.currency})")
    for ia in report.invoice_agings:
        mark = "FLAG" if ia.flagged else "    "
        print(
            f"  {mark}  {ia.invoice_id:<9} {ia.customer:<18} "
            f"{str(ia.amount):>12}  {ia.days_overdue:>4}d  {ia.bucket:<7} "
            f"{ia.tone_tier or ''}"
        )
        for reason in ia.flag_reasons:
            print(f"          · {reason}")

    header("Drafted dunning emails")
    if report.drafts_skipped_reason:
        print(f"  (skipped: {report.drafts_skipped_reason})")
    for d in report.drafts:
        print(f"  {d.invoice_id} [{d.tone}]  subject: {d.subject}")
        print("    " + d.body.replace("\n", "\n    "))
        if d.citations:
            for c in d.citations:
                print(f"    [{c}]")
        else:
            print("    (ungrounded — no policy excerpt cited)")
        print()

    req = run.approval_request
    header("Approval")
    print(f"  request id={req.id}, status={req.status}, stage={req.current_stage}")
    print(f"  summary: {report.to_dict()['summary']}")

    header("Audit log chain verification")
    print(f"  verify_chain() -> ok={audit_log.verify_chain().ok}")
    print(f"  {len(audit_log.get_all())} events recorded.")

    audit_log.close()
    approval_queue.close()
    header("Done")


if __name__ == "__main__":
    main()
