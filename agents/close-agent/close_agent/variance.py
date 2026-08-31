"""Deterministic budget-vs-actual variance computation and threshold flagging.

No LLM involvement (CLAUDE.md rule #4): the join, the subtraction, the
percentage, and every threshold comparison run in plain `Decimal` code so the
results are reproducible and testable. The model's only job, later, is to write
prose about the lines this module flags.

This is arithmetic on a period's own plan and actuals, not an accounting
treatment, so no ASC/IFRS reference is encoded (same as
`reconciliation_agent.matching` and `ap_agent.sanity`).
"""

from decimal import ROUND_HALF_UP, Decimal

from connectors import BudgetActualLine

from .models import FlagThresholds, LineVariance

_PCT_QUANTUM = Decimal("0.0001")


def _fmt_money(value: Decimal) -> str:
    return f"${value:,.2f}"


def _fmt_pct(ratio: Decimal) -> str:
    return f"{ratio * 100:.1f}%"


def compute_variances(
    budget: list[BudgetActualLine],
    actuals: list[BudgetActualLine],
    *,
    period: str,
    currency: str,
    thresholds: FlagThresholds,
) -> list[LineVariance]:
    """Join budget to actuals by (account, line_item) and compute one
    `LineVariance` per distinct key. A side with no row contributes 0.

    Output is sorted by (account, line_item) for a deterministic report.
    """
    by_key: dict[tuple[str, str], dict[str, BudgetActualLine]] = {}
    for line in budget:
        by_key.setdefault((line.account, line.line_item), {})["budget"] = line
    for line in actuals:
        by_key.setdefault((line.account, line.line_item), {})["actual"] = line

    results: list[LineVariance] = []
    for account, line_item in sorted(by_key):
        entry = by_key[(account, line_item)]
        b = entry.get("budget")
        a = entry.get("actual")

        budget_amount = b.amount if b is not None else Decimal("0")
        actual_amount = a.amount if a is not None else Decimal("0")
        category = (b.category if b is not None else "") or (a.category if a is not None else "")
        variance = actual_amount - budget_amount

        if budget_amount == 0:
            pct_variance = None
        else:
            pct_variance = (variance / budget_amount).quantize(_PCT_QUANTUM, rounding=ROUND_HALF_UP)

        if variance > 0:
            direction = "over_budget"
        elif variance < 0:
            direction = "under_budget"
        else:
            direction = "on_budget"

        if b is not None and a is not None:
            presence = "both"
        elif b is not None:
            presence = "budget_only"
        else:
            presence = "actual_only"

        reasons = _flag_reasons(
            budget_amount=budget_amount,
            actual_amount=actual_amount,
            variance=variance,
            pct_variance=pct_variance,
            presence=presence,
            thresholds=thresholds,
        )

        results.append(LineVariance(
            account=account,
            line_item=line_item,
            category=category,
            period=period,
            currency=currency,
            budget_amount=budget_amount,
            actual_amount=actual_amount,
            variance=variance,
            pct_variance=pct_variance,
            direction=direction,
            presence=presence,
            flagged=bool(reasons),
            flag_reasons=reasons,
        ))

    return results


def _flag_reasons(
    *,
    budget_amount: Decimal,
    actual_amount: Decimal,
    variance: Decimal,
    pct_variance: "Decimal | None",
    presence: str,
    thresholds: FlagThresholds,
) -> list[str]:
    pct_reason = None
    if (
        thresholds.pct is not None
        and pct_variance is not None
        and abs(pct_variance) >= thresholds.pct
    ):
        pct_reason = (
            f"absolute percentage variance {_fmt_pct(abs(pct_variance))} "
            f">= threshold {_fmt_pct(thresholds.pct)}"
        )

    amount_reason = None
    if thresholds.amount is not None and abs(variance) >= thresholds.amount:
        amount_reason = (
            f"absolute variance {_fmt_money(abs(variance))} "
            f">= threshold {_fmt_money(thresholds.amount)}"
        )

    reasons: list[str] = []
    if thresholds.combine == "all":
        if pct_reason is not None and amount_reason is not None:
            reasons = [pct_reason, amount_reason]
    else:  # "any"
        reasons = [r for r in (pct_reason, amount_reason) if r is not None]

    # Presence rules are independent of `combine` and of the numeric rules.
    if thresholds.flag_unbudgeted and presence == "actual_only" and actual_amount != 0:
        reasons.append(f"actual spend {_fmt_money(actual_amount)} against zero budget")
    if thresholds.flag_missing_actual and presence == "budget_only":
        reasons.append(f"budgeted {_fmt_money(budget_amount)} with no actual reported")

    return reasons
