"""Manual, real end-to-end check — makes one real Claude API call (forecast
narrative).

Not part of the automated eval suite: it lives outside evals/ and its filename
doesn't match pytest's discovery pattern, so it is never collected or run in CI.
Run it yourself to see the agent work live against a real model:

    ANTHROPIC_API_KEY=sk-... python agents/fpa-agent/manual_live_run.py

Uses throwaway in-memory audit-log / approval-queue stores (nothing persisted),
the committed synthetic historical-actuals fixtures
(evals/fixtures/actuals/*.csv — all fictional: 2026-04 .. 2026-06), and the
synthetic FP&A-methodology corpus from evals/fixtures.py. Runs with an
aggressive Revenue growth assumption so the high-sensitivity flag fires and the
narrative has to call it out.
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

from fpa_agent import ForecastAssumptions, run_driver_based_forecast  # noqa: E402
from fixtures import ALL_DOCUMENTS  # noqa: E402

ACTUALS_DIR = _ROOT / "evals" / "fixtures" / "actuals"
WIDTH = 72


def header(title: str) -> None:
    print("\n" + "=" * WIDTH)
    print(f" {title}")
    print("=" * WIDTH)


def main() -> None:
    print("=" * WIDTH)
    print(" LedgerMind — FP&A Agent Demo")
    print(" Deterministic driver-based projection + high-sensitivity flagging +")
    print(" a forecast narrative that frames assumptions as assumptions,")
    print(" gated on human approval.")
    print("=" * WIDTH)

    kb = KnowledgeBase()
    kb.ingest(ALL_DOCUMENTS)
    audit_log = AuditLogStore(":memory:")
    approval_queue = ApprovalQueue(":memory:", audit_log)
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment

    run = run_driver_based_forecast(
        source_system="demo_co",
        actuals_folder=ACTUALS_DIR,
        knowledge_base=kb,
        audit_log=audit_log,
        approval_queue=approval_queue,
        client=client,
        assumptions=ForecastAssumptions(
            default_growth=Decimal("0.02"),
            category_growth={"Revenue": Decimal("0.30")},
        ),
        horizon=3,
    )
    report = run.report

    header(f"Forecast — base {report.base_period}, projecting {report.projected_periods} ({report.currency})")
    seen = set()
    for pl in report.projected_lines:
        key = (pl.account, pl.line_item)
        if key in seen:
            continue
        seen.add(key)
        proj = ", ".join(
            str(x.projected_amount) for x in report.projected_lines if (x.account, x.line_item) == key
        )
        mark = "FLAG" if pl.flagged else "    "
        print(f"  {mark}  {pl.account:<5} {pl.line_item:<26} base {pl.base_amount:>12}  "
              f"@ {pl.growth_rate}/period ({pl.growth_source})  ->  {proj}")

    header("Flagged high-sensitivity lines")
    for key in sorted({(pl.account, pl.line_item) for pl in report.flagged}):
        reasons = sorted({r for pl in report.flagged if (pl.account, pl.line_item) == key for r in pl.flag_reasons})
        print(f"  {key[0]} {key[1]}")
        for r in reasons:
            print(f"    · {r}")

    header("Drafted narrative")
    if report.narrative_skipped_reason:
        print(f"  (skipped: {report.narrative_skipped_reason})")
    else:
        n = report.narrative
        print(f"  {n.summary}")
        print("\n  Assumptions (as stated by the model):")
        for a in n.assumptions_described:
            print(f"    - {a}")
        print("\n  Flagged items called out:")
        for f in n.flagged_items_called_out:
            print(f"    - {f}")
        if n.citations:
            for c in n.citations:
                print(f"    [{c}]")
        else:
            print("    (ungrounded — no methodology excerpt cited)")

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
