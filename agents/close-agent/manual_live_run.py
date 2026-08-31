"""Manual, real end-to-end check — makes one real Claude API call (variance
explanations).

Not part of the automated eval suite: it lives outside evals/ and its filename
doesn't match pytest's discovery pattern, so it is never collected or run in
CI. Run it yourself to see the agent work live against a real model:

    ANTHROPIC_API_KEY=sk-... python agents/close-agent/manual_live_run.py

Uses throwaway in-memory audit-log / approval-queue stores (nothing persisted),
the committed synthetic budget/actuals fixtures (evals/fixtures/{budget,actuals}/
— all fictional), and the synthetic accounting-policy corpus from
evals/fixtures.py. Runs the eventful 2026-07 period with an amount threshold of
$25,000 so the COGS line is flagged alongside the percentage breaches.
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

from close_agent import FlagThresholds, run_close_variance_analysis  # noqa: E402
from fixtures import ALL_DOCUMENTS  # noqa: E402

PERIOD = "2026-07"
BUDGET_DIR = _ROOT / "evals" / "fixtures" / "budget"
ACTUALS_DIR = _ROOT / "evals" / "fixtures" / "actuals"
WIDTH = 72


def header(title: str) -> None:
    print("\n" + "=" * WIDTH)
    print(f" {title}")
    print("=" * WIDTH)


def main() -> None:
    print("=" * WIDTH)
    print(" LedgerMind — Close Agent Demo")
    print(" Deterministic budget-vs-actual variances + threshold flagging +")
    print(" cited plain-English explanations, gated on human approval.")
    print("=" * WIDTH)

    kb = KnowledgeBase()
    kb.ingest(ALL_DOCUMENTS)
    audit_log = AuditLogStore(":memory:")
    approval_queue = ApprovalQueue(":memory:", audit_log)
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment

    run = run_close_variance_analysis(
        source_system="demo_co",
        period=PERIOD,
        budget_folder=BUDGET_DIR,
        actuals_folder=ACTUALS_DIR,
        knowledge_base=kb,
        audit_log=audit_log,
        approval_queue=approval_queue,
        client=client,
        thresholds=FlagThresholds(pct=Decimal("0.10"), amount=Decimal("25000")),
    )
    report = run.report

    header(f"Variance table — period {report.period} ({report.currency})")
    for lv in report.line_variances:
        pct = "   n/a" if lv.pct_variance is None else f"{lv.pct_variance * 100:6.1f}%"
        mark = "FLAG" if lv.flagged else "    "
        print(
            f"  {mark}  {lv.account:<5} {lv.line_item:<28} "
            f"budget {lv.budget_amount:>12,.2f}  actual {lv.actual_amount:>12,.2f}  "
            f"var {lv.variance:>12,.2f}  {pct}"
        )

    header("Flagged lines")
    for lv in report.flagged:
        print(f"  {lv.account} {lv.line_item} — {lv.direction}")
        for reason in lv.flag_reasons:
            print(f"    · {reason}")

    header("Drafted explanations")
    if report.explanations_skipped_reason:
        print(f"  (skipped: {report.explanations_skipped_reason})")
    for e in report.explanations:
        print(f"  {e.account} {e.line_item}:")
        print(f"    {e.explanation}")
        if e.primary_drivers:
            print(f"    drivers: {', '.join(e.primary_drivers)}")
        if e.citations:
            for c in e.citations:
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
