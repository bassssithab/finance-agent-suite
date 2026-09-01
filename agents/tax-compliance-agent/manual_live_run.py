"""Manual, real end-to-end check — makes one real Claude API call (filing-support
narrative).

Not part of the automated eval suite: it lives outside evals/ and its filename
doesn't match pytest's discovery pattern, so it is never collected or run in CI.
Run it yourself to see the agent work live against a real model:

    ANTHROPIC_API_KEY=sk-... python agents/tax-compliance-agent/manual_live_run.py

Uses throwaway in-memory audit-log / approval-queue stores (nothing persisted),
the committed synthetic transaction fixtures (evals/fixtures/transactions/*.csv
— all fictional), and the synthetic Larenthia VAT corpus from evals/fixtures.py.
Runs all three sample periods: a normal payable position, a refundable position,
and a batch with data-quality problems.
"""

import sys
import tempfile
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

from tax_compliance_agent import run_vat_provision  # noqa: E402
from fixtures import ALL_DOCUMENTS  # noqa: E402

TXN_DIR = _ROOT / "evals" / "fixtures" / "transactions"
SCENARIOS = ["normal_payable", "refundable_period", "data_quality_issues"]
WIDTH = 72


def header(title: str) -> None:
    print("\n" + "=" * WIDTH)
    print(f" {title}")
    print("=" * WIDTH)


def main() -> None:
    print("=" * WIDTH)
    print(" LedgerMind — Tax Compliance Agent Demo")
    print(" Deterministic period-end VAT provision + anomaly flagging +")
    print(" a filing-support narrative that never claims the return is ready,")
    print(" gated on human approval.")
    print("=" * WIDTH)

    kb = KnowledgeBase()
    kb.ingest(ALL_DOCUMENTS)
    audit_log = AuditLogStore(":memory:")
    approval_queue = ApprovalQueue(":memory:", audit_log)
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment

    for scenario in SCENARIOS:
        header(f"Period: {scenario}")
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / f"{scenario}.csv").write_text((TXN_DIR / f"{scenario}.csv").read_text())

            run = run_vat_provision(
                source_system="demo_co",
                transactions_folder=folder,
                knowledge_base=kb,
                audit_log=audit_log,
                approval_queue=approval_queue,
                client=client,
            )
        report = run.report

        print(f"  Period:      {report.period_label} ({report.currency})")
        print(f"  Output VAT:  {report.output_vat_total}")
        print(f"  Input VAT:   {report.input_vat_total}")
        print(f"  Net VAT:     {report.net_vat}   -> {report.position.upper()}")

        excluded = [ct.transaction_id for ct in report.computed_transactions if not ct.included_in_totals]
        if excluded:
            print(f"  Excluded from totals: {', '.join(excluded)}")

        if report.anomalies:
            print("  Anomalies:")
            for a in report.anomalies:
                scope = a.transaction_id or "period"
                print(f"    · [{a.code}] ({scope}) {a.detail}")
        else:
            print("  Anomalies:   none")

        if report.narrative_skipped_reason:
            print(f"  Narrative:   skipped ({report.narrative_skipped_reason})")
        else:
            n = report.narrative
            print(f"  Narrative:   {n.position_summary}")
            for e in n.anomaly_explanations:
                print(f"    - {e}")
            print(f"  Specialist review needed: {n.specialist_review_needed}")
            for c in n.citations:
                print(f"    [{c}]")
            if not n.citations:
                print("    (ungrounded — no filing-guidance excerpt cited)")

        req = run.approval_request
        print(f"  Approval:    request id={req.id}, status={req.status}, stage={req.current_stage}")

    header("Audit log chain verification")
    print(f"  verify_chain() -> ok={audit_log.verify_chain().ok}")
    print(f"  {len(audit_log.get_all())} events recorded across {len(SCENARIOS)} periods.")

    audit_log.close()
    approval_queue.close()
    header("Done")


if __name__ == "__main__":
    main()
