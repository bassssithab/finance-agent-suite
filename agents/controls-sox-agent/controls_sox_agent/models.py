"""Data model for the controls-sox-agent journal-entry SoD workflow: the control
policy, one entry's test result, one drafted deficiency narrative, and the
control-test report that goes to approvals.

A `ControlTestReport` is never a final controls conclusion by itself — CLAUDE.md
rule #2 requires it to go through `platform/approvals` before anything is acted
on (a deficiency logged, an entry challenged). This module only shapes the data
and its audit/approval payloads; the deterministic pass/fail test lives in
`sod.py` (plain code, rule #4), the narrative drafting in `narrate.py` (the only
LLM call), orchestration in `runner.py`.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

# ---------------------------------------------------------------------------
# Control policy (consumed by sod.py) — configurable per run
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ControlPolicy:
    """The company-policy inputs to the segregation-of-duties test.

    `dual_approval_threshold` is compared against the absolute entry amount: an
    entry whose magnitude is >= this needs two distinct approvers. The boundary
    is inclusive — an entry exactly at the threshold requires dual approval.

    `require_distinct_approvers` enforces, at ANY amount, that the preparer is
    not also an approver and that the two named approvers differ; only the
    *requirement* of a second approver is threshold-gated.
    """

    dual_approval_threshold: Decimal = Decimal("50000")
    require_distinct_approvers: bool = True

    def to_dict(self) -> dict:
        return {
            "dual_approval_threshold": str(self.dual_approval_threshold),
            "require_distinct_approvers": self.require_distinct_approvers,
        }


# ---------------------------------------------------------------------------
# One control exception on one entry (produced by sod.py) — pure code, no LLM
# ---------------------------------------------------------------------------

# Violation codes, each at most once per entry:
#   "no_approver"             — the entry has no recorded approver at all
#   "preparer_is_approver"    — the preparer also appears as an approver
#   "duplicate_approvers"     — approver_1 and approver_2 are the same person
#   "missing_second_approver" — at/above the dual-approval threshold with one approver
VIOLATION_CODES = (
    "no_approver",
    "preparer_is_approver",
    "duplicate_approvers",
    "missing_second_approver",
)


@dataclass(frozen=True)
class Violation:
    entry_id: str
    code: str
    detail: str  # deterministic human-readable reason, safe to drop into the audit log

    def to_dict(self) -> dict:
        return {"entry_id": self.entry_id, "code": self.code, "detail": self.detail}


@dataclass(frozen=True)
class ControlTestResult:
    """The segregation-of-duties test outcome for one journal entry."""

    entry_id: str
    date: str  # ISO date, as normalized by the connector
    account: str
    amount: Decimal
    currency: str
    preparer: str
    approver_1: Optional[str]
    approver_2: Optional[str]
    dual_approval_required: bool
    passed: bool
    violations: list[Violation]

    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "date": self.date,
            "account": self.account,
            "amount": str(self.amount),
            "currency": self.currency,
            "preparer": self.preparer,
            "approver_1": self.approver_1,
            "approver_2": self.approver_2,
            "dual_approval_required": self.dual_approval_required,
            "passed": self.passed,
            "violations": [v.to_dict() for v in self.violations],
        }


# ---------------------------------------------------------------------------
# One drafted deficiency narrative (produced by narrate.py from the tool call)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeficiencyNarrative:
    entry_id: str
    violation_code: str
    narrative: str
    citations: list[str]      # citation labels relied on; [] when ungrounded
    remediation: list[str]    # short remediation-step phrases, where identifiable

    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "violation_code": self.violation_code,
            "narrative": self.narrative,
            "citations": list(self.citations),
            "remediation": list(self.remediation),
        }


# ---------------------------------------------------------------------------
# The control-test report submitted for approval
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ControlTestReport:
    control_id: str
    control_name: str
    source_system: str
    generated_at: str
    currency: str
    policy: dict
    results: list[ControlTestResult]           # every entry tested
    narratives: list[DeficiencyNarrative]      # one per violation; [] if skipped
    narratives_skipped_reason: Optional[str]   # None if drafting ran and parsed
    model: Optional[str]
    narrative_prompt_hash: Optional[str]
    narrative_chunk_ids: list[str]
    narrative_citations: list[str]

    @property
    def violations(self) -> list[Violation]:
        return [v for r in self.results for v in r.violations]

    def summary(self) -> dict:
        violations = self.violations
        by_code: dict[str, int] = {}
        for v in violations:
            by_code[v.code] = by_code.get(v.code, 0) + 1
        total_amount = sum((abs(r.amount) for r in self.results), Decimal("0"))
        return {
            "entries_tested": len(self.results),
            "entries_with_violations": sum(1 for r in self.results if not r.passed),
            "violation_count": len(violations),
            "violations_by_code": by_code,
            "dual_approval_required_count": sum(
                1 for r in self.results if r.dual_approval_required
            ),
            "total_amount_tested": str(total_amount),
        }

    def to_dict(self) -> dict:
        return {
            "control_id": self.control_id,
            "control_name": self.control_name,
            "source_system": self.source_system,
            "generated_at": self.generated_at,
            "currency": self.currency,
            "policy": self.policy,
            "summary": self.summary(),
            "results": [r.to_dict() for r in self.results],
            "narratives": [n.to_dict() for n in self.narratives],
            "narratives_skipped_reason": self.narratives_skipped_reason,
            "model": self.model,
            "narrative_prompt_hash": self.narrative_prompt_hash,
            "narrative_chunk_ids": list(self.narrative_chunk_ids),
            "narrative_citations": list(self.narrative_citations),
        }
