"""Deterministic arithmetic check on an extracted invoice.

CLAUDE.md rule #4: calculations and totals run in plain code with tests — the
LLM transcribes the invoice, this module does the maths. It never corrects the
numbers; it reports whether they add up so a human reviewer (and the approval
payload) can see any discrepancy.

This is arithmetic reconciliation of a document's own figures, not an
accounting treatment — no ASC/IFRS standard governs it (same as
`reconciliation_agent.matching`).
"""

from decimal import Decimal

from .models import ExtractedInvoice, LineTotalIssue, SanityCheckResult

# quantity * unit_price is compared to the stated line_total with a small
# tolerance: invoices routinely round each line to the cent, so a penny of
# drift is a rounding artefact, not a discrepancy. The line-sum vs. grand-total
# check below is exact — those are both figures printed on the invoice.
LINE_ROUNDING_TOLERANCE = Decimal("0.01")


def check_invoice_totals(invoice: ExtractedInvoice) -> SanityCheckResult:
    computed_line_sum = sum(
        (li.line_total for li in invoice.line_items), Decimal("0")
    )
    difference = computed_line_sum - invoice.grand_total

    line_total_issues: list[LineTotalIssue] = []
    for index, li in enumerate(invoice.line_items):
        computed = li.quantity * li.unit_price
        delta = computed - li.line_total
        if abs(delta) > LINE_ROUNDING_TOLERANCE:
            line_total_issues.append(
                LineTotalIssue(
                    line_index=index,
                    description=li.description,
                    quantity=li.quantity,
                    unit_price=li.unit_price,
                    stated_line_total=li.line_total,
                    computed_line_total=computed,
                    difference=delta,
                )
            )

    ok = difference == 0 and not line_total_issues
    return SanityCheckResult(
        ok=ok,
        computed_line_sum=computed_line_sum,
        stated_grand_total=invoice.grand_total,
        difference=difference,
        line_total_issues=line_total_issues,
    )
