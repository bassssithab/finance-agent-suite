"""Deterministic accounts-receivable aging and dunning-flagging.

No LLM involvement (CLAUDE.md rule #4): days-overdue arithmetic, bucket
assignment, every flagging-rule comparison and the tone-tier assignment run in
plain `date`/`int`/`Decimal` code so the result for every invoice is
reproducible and testable. The model's only job, later, is to write the dunning
email for each invoice this module flags, in the tone tier this module assigns.

This is arithmetic on invoice dates and a company dunning policy, not an
accounting treatment, so no ASC/IFRS reference is encoded (same as
`reconciliation_agent.matching`, `ap_agent.sanity` and `close_agent.variance`).
"""

from datetime import date
from decimal import Decimal

from connectors import OpenInvoice

from .models import (
    BUCKET_1_30,
    BUCKET_31_60,
    BUCKET_61_90,
    BUCKET_90_PLUS,
    BUCKET_CURRENT,
    DunningPolicy,
    InvoiceAging,
    TONE_FIRM,
    TONE_FORMAL,
    TONE_REMINDER,
)


def _bucket(days_overdue: int) -> str:
    if days_overdue <= 0:
        return BUCKET_CURRENT
    if days_overdue <= 30:
        return BUCKET_1_30
    if days_overdue <= 60:
        return BUCKET_31_60
    if days_overdue <= 90:
        return BUCKET_61_90
    return BUCKET_90_PLUS


def _tone_tier(days_overdue: int) -> str:
    """Gentle up to 60 days, firmer at 61-90, most formal past 90 — matches the
    escalation the README describes."""
    if days_overdue <= 60:
        return TONE_REMINDER
    if days_overdue <= 90:
        return TONE_FIRM
    return TONE_FORMAL


def _fmt_money(value: Decimal) -> str:
    return f"${value:,.2f}"


def compute_aging(
    invoices: list[OpenInvoice],
    *,
    as_of_date: date,
    policy: DunningPolicy,
) -> list[InvoiceAging]:
    """One `InvoiceAging` per open invoice.

    Output is sorted by (customer, due_date, invoice_id) for a deterministic
    report.
    """
    # First pass: how many overdue invoices each customer has, so the
    # repeat-customer rule can be applied per invoice in the second pass.
    overdue_by_customer: dict[str, int] = {}
    for inv in invoices:
        if (as_of_date - inv.due_date).days >= 1:
            overdue_by_customer[inv.customer] = overdue_by_customer.get(inv.customer, 0) + 1

    results: list[InvoiceAging] = []
    for inv in sorted(invoices, key=lambda i: (i.customer, i.due_date, i.invoice_id)):
        days_overdue = (as_of_date - inv.due_date).days
        bucket = _bucket(days_overdue)

        days_since_last_payment = (
            (as_of_date - inv.last_payment_date).days
            if inv.last_payment_date is not None
            else None
        )

        reasons = _flag_reasons(
            inv=inv,
            days_overdue=days_overdue,
            customer_overdue_count=overdue_by_customer.get(inv.customer, 0),
            policy=policy,
        )
        flagged = bool(reasons)

        results.append(InvoiceAging(
            invoice_id=inv.invoice_id,
            customer=inv.customer,
            invoice_date=inv.invoice_date.isoformat(),
            due_date=inv.due_date.isoformat(),
            amount=inv.amount,
            currency=inv.currency,
            last_payment_date=(
                inv.last_payment_date.isoformat() if inv.last_payment_date is not None else None
            ),
            days_overdue=days_overdue,
            days_since_last_payment=days_since_last_payment,
            bucket=bucket,
            flagged=flagged,
            flag_reasons=reasons,
            tone_tier=_tone_tier(days_overdue) if flagged else None,
        ))

    return results


def _flag_reasons(
    *,
    inv: OpenInvoice,
    days_overdue: int,
    customer_overdue_count: int,
    policy: DunningPolicy,
) -> list[str]:
    # A balance below `min_amount` is never chased, whatever the age.
    if policy.min_amount is not None and abs(inv.amount) < policy.min_amount:
        return []

    reasons: list[str] = []

    if days_overdue >= policy.min_days_overdue:
        reasons.append(
            f"{days_overdue} days overdue >= threshold {policy.min_days_overdue}"
        )

    if (
        policy.flag_repeat_customers
        and days_overdue >= 1
        and customer_overdue_count >= policy.repeat_customer_min_overdue_invoices
    ):
        reasons.append(
            f"customer {inv.customer!r} has {customer_overdue_count} overdue invoices "
            f"(>= threshold {policy.repeat_customer_min_overdue_invoices})"
        )

    return reasons
