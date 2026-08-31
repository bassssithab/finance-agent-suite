"""Manual, real end-to-end check — makes one real Claude API call (deficiency
narratives).

Not part of the automated eval suite: it lives outside evals/ and its filename
doesn't match pytest's discovery pattern, so it is never collected or run in
CI. Run it yourself to see the agent work live against a real model:

    ANTHROPIC_API_KEY=sk-... python agents/controls-sox-agent/manual_live_run.py

Uses throwaway in-memory audit-log / approval-queue stores (nothing persisted),
the committed synthetic journal-entry fixtures (evals/fixtures/journal_entries/
— all fictional: a clean batch, a batch of SoD violations, and edge cases), and
the synthetic internal-controls-policy corpus from evals/fixtures.py.
"""

import sys
from decimal import Decimal
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

from controls_sox_agent import ControlPolicy, run_journal_entry_control_test  # noqa: E402
from fixtures import ALL_DOCUMENTS  # noqa: E402

ENTRIES_DIR = _ROOT / "evals" / "fixtures" / "journal_entries"
WIDTH = 72


def header(title: str) -> None:
    print("\n" + "=" * WIDTH)
    print(f" {title}")
    print("=" * WIDTH)


def main() -> None:
    print("=" * WIDTH)
    print(" LedgerMind — Controls (SOX) Agent Demo")
    print(" Deterministic segregation-of-duties test over journal-entry")
    print(" approvals + cited deficiency narratives, gated on human approval.")
    print("=" * WIDTH)

    kb = KnowledgeBase()
    kb.ingest(ALL_DOCUMENTS)
    audit_log = AuditLogStore(":memory:")
    approval_queue = ApprovalQueue(":memory:", audit_log)
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment

    run = run_journal_entry_control_test(
        source_system="demo_co",
        entries_folder=ENTRIES_DIR,
        knowledge_base=kb,
        audit_log=audit_log,
        approval_queue=approval_queue,
        client=client,
        policy=ControlPolicy(dual_approval_threshold=Decimal("50000")),
    )
    report = run.report

    header(f"Control {report.control_id} — {report.control_name}")
    for r in report.results:
        mark = "FAIL" if not r.passed else "    "
        approvers = r.approver_1 or "(none)"
        if r.approver_2:
            approvers += f", {r.approver_2}"
        print(
            f"  {mark}  {r.entry_id:<8} {str(r.amount):>14} {r.currency}  "
            f"prep {r.preparer:<12} appr {approvers}"
        )
        for v in r.violations:
            print(f"          · {v.code}: {v.detail}")

    header("Drafted deficiency narratives")
    if report.narratives_skipped_reason:
        print(f"  (skipped: {report.narratives_skipped_reason})")
    for n in report.narratives:
        print(f"  {n.entry_id} [{n.violation_code}]:")
        print(f"    {n.narrative}")
        if n.remediation:
            print(f"    remediation: {', '.join(n.remediation)}")
        if n.citations:
            for c in n.citations:
                print(f"    [{c}]")
        else:
            print("    (ungrounded — no policy excerpt cited)")

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
