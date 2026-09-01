"""Deterministic expense-policy compliance check on an extracted receipt.

No LLM (CLAUDE.md rule #4): parsing the receipt date, the per-category limit
comparison, the receipt-age comparison and the required-fields check all run in
plain `date`/`Decimal` code, so the pass/fail decision for every receipt is
reproducible and testable. The model's only later job (in `explain.py`) is to
write the cited narrative for the violations this module flags.

This is arithmetic and date math against a company travel-and-expense policy,
not an accounting treatment, so no ASC/IFRS reference is encoded (same stance as
`reconciliation_agent.matching`, `ap_agent.sanity` and `close_agent.variance`).
The dollar limits and the maximum receipt age are company-policy inputs
(`ExpensePolicy`), not standards.

Category matching is exact after `strip()` + `casefold()` ("Meals " and "meals"
are the same category). A label the policy's `category_limits` map does not
contain falls back to `default_limit`, or is uncapped when that is None — a
deliberate trade-off, documented in the README: the agent does not try to guess
that "Team dinner" means "meals".
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from .models import ComplianceCheckResult, ExpensePolicy, ExtractedReceipt, Violation


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


def _normalize(label: str) -> str:
    return label.strip().casefold()


def _parse_receipt_date(raw: str, formats: tuple) -> Optional[date]:
    """ISO first, then each configured strptime format. None if nothing matches."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _missing_required_fields(receipt: ExtractedReceipt, required: tuple) -> list[str]:
    present = {
        "vendor": bool(receipt.vendor.strip()),
        "date": bool(receipt.date.strip()),
        "amount": receipt.amount > 0,
        "currency": bool(receipt.currency.strip()),
        "expense_category": bool(receipt.expense_category.strip()),
    }
    return [f for f in required if f in present and not present[f]]


def _limit_for_category(receipt: ExtractedReceipt, policy: ExpensePolicy) -> Optional[Decimal]:
    """The limit that applies to this receipt's category, or None when uncapped.

    None is also returned when the category field is empty — the missing-field
    check owns that case.
    """
    if not receipt.expense_category.strip():
        return None
    normalized = {_normalize(k): v for k, v in policy.category_limits.items()}
    hit = normalized.get(_normalize(receipt.expense_category))
    if hit is not None:
        return hit
    return policy.default_limit


def check_compliance(
    receipt: ExtractedReceipt,
    policy: ExpensePolicy,
    *,
    as_of_date: date,
) -> ComplianceCheckResult:
    violations: list[Violation] = []

    # --- required fields --------------------------------------------------
    for missing in _missing_required_fields(receipt, policy.required_fields):
        violations.append(Violation(
            code="missing_required_field",
            field=missing,
            detail=f"required field {missing!r} is missing from the extracted receipt",
        ))

    # --- receipt date + age --------------------------------------------------
    parsed_date = _parse_receipt_date(receipt.date, policy.date_formats)
    if receipt.date.strip() and parsed_date is None:
        violations.append(Violation(
            code="date_unparseable",
            field="date",
            detail=(
                f"receipt date {receipt.date!r} could not be parsed with the "
                f"configured formats {list(policy.date_formats)}; the age check was skipped"
            ),
        ))

    if (
        parsed_date is not None
        and policy.max_receipt_age_days is not None
    ):
        age_days = (as_of_date - parsed_date).days
        if age_days > policy.max_receipt_age_days:
            violations.append(Violation(
                code="receipt_too_old",
                field="date",
                detail=(
                    f"receipt dated {parsed_date.isoformat()} is {age_days} days old, "
                    f"over the {policy.max_receipt_age_days}-day limit "
                    f"(as of {as_of_date.isoformat()})"
                ),
            ))

    # --- category spending limit -------------------------------------------
    applied_limit = _limit_for_category(receipt, policy)
    if applied_limit is not None and receipt.amount > applied_limit:
        over_by = receipt.amount - applied_limit
        violations.append(Violation(
            code="category_over_limit",
            field="amount",
            detail=(
                f"{receipt.expense_category.strip()} expense "
                f"{_money(receipt.amount)} {receipt.currency.strip() or '(no currency)'} "
                f"exceeds the {_money(applied_limit)} limit for category "
                f"{_normalize(receipt.expense_category)!r} (over by {_money(over_by)})"
            ),
        ))

    return ComplianceCheckResult(
        passed=not violations,
        violations=violations,
        parsed_date=parsed_date.isoformat() if parsed_date is not None else None,
        applied_limit=applied_limit,
        as_of_date=as_of_date.isoformat(),
    )
