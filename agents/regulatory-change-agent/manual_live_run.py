"""Manual, real end-to-end check — makes one real Claude API call (impact
assessment).

Not part of the automated eval suite: it lives outside evals/ and its filename
doesn't match pytest's discovery pattern, so it is never collected or run in CI.
Run it yourself to see the agent work live against a real model:

    ANTHROPIC_API_KEY=sk-... python agents/regulatory-change-agent/manual_live_run.py

Uses throwaway in-memory audit-log / approval-queue stores (nothing persisted),
the committed synthetic controls register
(evals/fixtures/controls/company_controls.csv — all fictional) and the synthetic
regulatory-guidance corpus from evals/fixtures.py. Runs all three sample
scenarios: a clear existing-control match, a genuine gap, and an ambiguous case.
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

from regulatory_change_agent import run_change_triage  # noqa: E402
from fixtures import ALL_DOCUMENTS, SCENARIOS  # noqa: E402

CONTROLS_DIR = _ROOT / "evals" / "fixtures" / "controls"
WIDTH = 72


def header(title: str) -> None:
    print("\n" + "=" * WIDTH)
    print(f" {title}")
    print("=" * WIDTH)


def main() -> None:
    print("=" * WIDTH)
    print(" LedgerMind — Regulatory Change Agent Demo")
    print(" First-pass keyword/category triage of a new requirement against")
    print(" the controls register + an impact-assessment narrative that never")
    print(" concludes on compliance, gated on human approval.")
    print("=" * WIDTH)

    kb = KnowledgeBase()
    kb.ingest(ALL_DOCUMENTS)
    audit_log = AuditLogStore(":memory:")
    approval_queue = ApprovalQueue(":memory:", audit_log)
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment

    for name, sc in SCENARIOS.items():
        header(f"Scenario: {name}  [{sc['requirement_reference']}]")
        print(f"  requirement: {sc['requirement_text']}")

        run = run_change_triage(
            source_system="demo_co",
            requirement_text=sc["requirement_text"],
            controls_folder=CONTROLS_DIR,
            knowledge_base=kb,
            audit_log=audit_log,
            approval_queue=approval_queue,
            requirement_reference=sc["requirement_reference"],
            client=client,
        )
        report = run.report

        print(f"  verdict:     {report.coverage_verdict}   (gap flagged for review: {report.gap_flagged})")
        for r in report.flag_reasons:
            print(f"    · {r}")
        print("  surfaced controls (keyword shortlist — not confirmed):")
        for cr in report.relevant_controls:
            print(f"    {cr.control_id}  score {cr.score}  matched {cr.matched_terms}")
        if not report.relevant_controls:
            print("    (none)")

        if report.narrative_skipped_reason:
            print(f"  narrative:   skipped ({report.narrative_skipped_reason})")
        else:
            n = report.narrative
            print(f"  assessment:  {n.assessment}")
            for e in n.relevant_controls_explained:
                print(f"    - {e['control_id']}: {e['explanation']}")
            if n.gap_explanation:
                print(f"    gap: {n.gap_explanation}")
            print(f"    review required: {n.review_required_statement}")
            for c in n.citations:
                print(f"    [{c}]")
            if not n.citations:
                print("    (ungrounded — no guidance excerpt cited)")

        req = run.approval_request
        print(f"  approval:    request id={req.id}, status={req.status}, stage={req.current_stage}")

    header("Audit log chain verification")
    print(f"  verify_chain() -> ok={audit_log.verify_chain().ok}")
    print(f"  {len(audit_log.get_all())} events recorded across {len(SCENARIOS)} scenarios.")

    audit_log.close()
    approval_queue.close()
    header("Done")


if __name__ == "__main__":
    main()
