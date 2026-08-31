"""Deterministic segregation-of-duties test over journal-entry approvals.

No LLM (CLAUDE.md rule #4): normalizing names, comparing the preparer to each
approver, checking the two approvers are distinct, and the threshold comparison
that decides whether a second approver was required all run in plain code, so
the pass/fail decision for every entry is reproducible and testable. The model's
only later job (in `narrate.py`) is to write the deficiency narrative for the
entries this module flags.

The control encoded here — segregation of duties over journal-entry approval —
is an internal-controls concept (COSO 2013 *Internal Control — Integrated
Framework*, Control Activities, principle 10; PCAOB AS 2201 / SOX Section 404),
not an ASC/IFRS accounting treatment, so no accounting-standard reference is
encoded (same stance as `reconciliation_agent.matching`, `ap_agent.sanity` and
`close_agent.variance`). The dollar threshold for requiring a second approver is
a company-policy input (`ControlPolicy`), not a standard.

Name comparison is case- and surrounding-whitespace-insensitive
(`strip()` + `casefold()`): "J.Smith " and "j.smith" are treated as the same
person. This is a deliberate trade-off — it catches trivial spoofing and
data-entry drift at the cost of merging two genuine ids that differ only by case.
"""

from decimal import Decimal
from typing import Optional

from connectors import JournalEntry

from .models import ControlPolicy, ControlTestResult, Violation


def _normalize(name: Optional[str]) -> str:
    return name.strip().casefold() if name else ""


def _fmt_money(value: Decimal) -> str:
    return f"${value:,.2f}"


def check_segregation_of_duties(
    entries: list[JournalEntry], policy: ControlPolicy
) -> list[ControlTestResult]:
    """One `ControlTestResult` per entry, in the order given by the connector."""
    return [_evaluate_entry(entry, policy) for entry in entries]


def _evaluate_entry(entry: JournalEntry, policy: ControlPolicy) -> ControlTestResult:
    preparer_n = _normalize(entry.preparer)
    named = [
        (slot, raw, _normalize(raw))
        for slot, raw in (("approver_1", entry.approver_1), ("approver_2", entry.approver_2))
        if _normalize(raw)
    ]
    magnitude = abs(entry.amount)
    dual_required = magnitude >= policy.dual_approval_threshold

    violations: list[Violation] = []

    if not named:
        violations.append(Violation(
            entry_id=entry.entry_id,
            code="no_approver",
            detail=f"entry {entry.entry_id} ({_fmt_money(magnitude)}) has no recorded approver",
        ))
    else:
        self_slots = [slot for slot, _raw, n in named if n == preparer_n]
        if self_slots:
            violations.append(Violation(
                entry_id=entry.entry_id,
                code="preparer_is_approver",
                detail=(
                    f"preparer {entry.preparer!r} also appears as "
                    f"{' and '.join(self_slots)} on entry {entry.entry_id}"
                ),
            ))

        n1, n2 = _normalize(entry.approver_1), _normalize(entry.approver_2)
        if policy.require_distinct_approvers and n1 and n2 and n1 == n2:
            violations.append(Violation(
                entry_id=entry.entry_id,
                code="duplicate_approvers",
                detail=(
                    f"approver_1 and approver_2 on entry {entry.entry_id} are the "
                    f"same person ({entry.approver_1!r})"
                ),
            ))

        if dual_required and len(named) < 2:
            violations.append(Violation(
                entry_id=entry.entry_id,
                code="missing_second_approver",
                detail=(
                    f"entry {entry.entry_id} ({_fmt_money(magnitude)}) is at or above the "
                    f"dual-approval threshold {_fmt_money(policy.dual_approval_threshold)} "
                    f"but has only one approver"
                ),
            ))

    return ControlTestResult(
        entry_id=entry.entry_id,
        date=entry.date.isoformat(),
        account=entry.account,
        amount=entry.amount,
        currency=entry.currency,
        preparer=entry.preparer,
        approver_1=entry.approver_1,
        approver_2=entry.approver_2,
        dual_approval_required=dual_required,
        passed=not violations,
        violations=violations,
    )
