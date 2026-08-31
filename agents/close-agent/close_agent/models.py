"""Data model for the close-agent variance workflow: the flagging thresholds,
one computed line variance, one drafted explanation, and the report that goes
to approvals.

A `VarianceReport` is never a final close position by itself — CLAUDE.md rule
#2 requires it to go through `platform/approvals` before anything is acted on.
This module only shapes the data and its audit/approval payloads; the variance
arithmetic lives in `variance.py` (plain code, rule #4), the explanation
drafting in `explain.py` (the only LLM call), orchestration in `runner.py`.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

# ---------------------------------------------------------------------------
# Flagging thresholds (consumed by variance.py) — all configurable per run
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FlagThresholds:
    """When a computed variance is large enough to warrant a drafted explanation.

    `pct` and `amount` are compared against the absolute variance. Either can be
    `None` to disable that rule. `combine` decides how the two numeric rules
    interact: "any" flags if either is breached, "all" flags only if both are.
    The two presence rules are independent safety nets and always apply.
    """

    pct: Optional[Decimal] = Decimal("0.10")   # |pct variance| >= 10%
    amount: Optional[Decimal] = None           # |$ variance| >= this
    combine: str = "any"                       # "any" | "all"
    flag_unbudgeted: bool = True               # actual spend against a zero budget
    flag_missing_actual: bool = True           # a budgeted line with no actual reported

    def to_dict(self) -> dict:
        return {
            "pct": str(self.pct) if self.pct is not None else None,
            "amount": str(self.amount) if self.amount is not None else None,
            "combine": self.combine,
            "flag_unbudgeted": self.flag_unbudgeted,
            "flag_missing_actual": self.flag_missing_actual,
        }


# ---------------------------------------------------------------------------
# One line's variance (produced by variance.py) — pure arithmetic, no LLM
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LineVariance:
    account: str
    line_item: str
    category: str
    period: str
    currency: str
    budget_amount: Decimal
    actual_amount: Decimal
    variance: Decimal                 # actual_amount - budget_amount
    pct_variance: Optional[Decimal]   # variance / budget_amount; None when budget is 0
    direction: str                    # "over_budget" | "under_budget" | "on_budget"
    presence: str                     # "both" | "budget_only" | "actual_only"
    flagged: bool
    flag_reasons: list[str]

    def to_dict(self) -> dict:
        return {
            "account": self.account,
            "line_item": self.line_item,
            "category": self.category,
            "period": self.period,
            "currency": self.currency,
            "budget_amount": str(self.budget_amount),
            "actual_amount": str(self.actual_amount),
            "variance": str(self.variance),
            "pct_variance": str(self.pct_variance) if self.pct_variance is not None else None,
            "direction": self.direction,
            "presence": self.presence,
            "flagged": self.flagged,
            "flag_reasons": list(self.flag_reasons),
        }


# ---------------------------------------------------------------------------
# One drafted explanation (produced by explain.py from the model's tool call)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VarianceExplanation:
    account: str
    line_item: str
    explanation: str
    citations: list[str]          # citation labels relied on; [] when ungrounded
    primary_drivers: list[str]

    def to_dict(self) -> dict:
        return {
            "account": self.account,
            "line_item": self.line_item,
            "explanation": self.explanation,
            "citations": list(self.citations),
            "primary_drivers": list(self.primary_drivers),
        }


# ---------------------------------------------------------------------------
# The report submitted for approval
# ---------------------------------------------------------------------------


def _line_ref(lv: Optional[LineVariance]) -> Optional[dict]:
    if lv is None:
        return None
    return {"account": lv.account, "line_item": lv.line_item, "variance": str(lv.variance)}


@dataclass(frozen=True)
class VarianceReport:
    period: str
    source_system: str
    generated_at: str
    currency: str
    thresholds: dict
    line_variances: list[LineVariance]           # every line item
    explanations: list[VarianceExplanation]      # flagged lines only; [] if skipped
    explanations_skipped_reason: Optional[str]   # None if drafting ran and parsed
    model: Optional[str]
    explanation_prompt_hash: Optional[str]
    explanation_chunk_ids: list[str]
    explanation_citations: list[str]

    @property
    def flagged(self) -> list[LineVariance]:
        return [lv for lv in self.line_variances if lv.flagged]

    def summary(self) -> dict:
        total_budget = sum((lv.budget_amount for lv in self.line_variances), Decimal("0"))
        total_actual = sum((lv.actual_amount for lv in self.line_variances), Decimal("0"))
        over = [lv for lv in self.line_variances if lv.variance > 0]
        under = [lv for lv in self.line_variances if lv.variance < 0]
        return {
            "line_count": len(self.line_variances),
            "flagged_count": len(self.flagged),
            "total_budget": str(total_budget),
            "total_actual": str(total_actual),
            "total_variance": str(total_actual - total_budget),
            "largest_over_budget": _line_ref(max(over, key=lambda lv: lv.variance, default=None)),
            "largest_under_budget": _line_ref(min(under, key=lambda lv: lv.variance, default=None)),
        }

    def to_dict(self) -> dict:
        return {
            "period": self.period,
            "source_system": self.source_system,
            "generated_at": self.generated_at,
            "currency": self.currency,
            "thresholds": self.thresholds,
            "summary": self.summary(),
            "line_variances": [lv.to_dict() for lv in self.line_variances],
            "explanations": [e.to_dict() for e in self.explanations],
            "explanations_skipped_reason": self.explanations_skipped_reason,
            "model": self.model,
            "explanation_prompt_hash": self.explanation_prompt_hash,
            "explanation_chunk_ids": list(self.explanation_chunk_ids),
            "explanation_citations": list(self.explanation_citations),
        }
