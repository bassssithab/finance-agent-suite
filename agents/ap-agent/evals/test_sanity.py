from decimal import Decimal

from ap_agent.models import ExtractedInvoice, InvoiceLineItem
from ap_agent.sanity import check_invoice_totals
from fixtures import load_expected


def _invoice(line_items, grand_total, confidence=0.95):
    return ExtractedInvoice(
        vendor_name="Test Vendor",
        invoice_number="T-1",
        invoice_date="2026-01-01",
        currency="USD",
        line_items=line_items,
        grand_total=Decimal(grand_total),
        extraction_confidence=confidence,
    )


def _line(qty, price, total, description="widget"):
    return InvoiceLineItem(
        description=description,
        quantity=Decimal(qty),
        unit_price=Decimal(price),
        line_total=Decimal(total),
    )


def test_clean_invoice_reconciles():
    result = check_invoice_totals(
        _invoice([_line("2", "10.00", "20.00"), _line("1", "5.50", "5.50")], "25.50")
    )

    assert result.ok is True
    assert result.difference == 0
    assert result.line_total_issues == []
    assert result.computed_line_sum == Decimal("25.50")


def test_wrong_grand_total_flagged_with_exact_difference():
    result = check_invoice_totals(
        _invoice([_line("1", "1240.00", "1240.00"), _line("1", "186.00", "186.00")], "1409.00")
    )

    assert result.ok is False
    # computed 1426.00 - stated 1409.00
    assert result.difference == Decimal("17.00")
    assert result.line_total_issues == []  # each line is internally consistent


def test_line_total_inconsistent_with_quantity_times_price_is_flagged():
    result = check_invoice_totals(
        _invoice([_line("3", "10.00", "35.00", description="overcharged line")], "35.00")
    )

    # The grand total matches the (wrong) line total, so the sum reconciles...
    assert result.difference == 0
    # ...but the line itself doesn't: 3 * 10.00 = 30.00, not 35.00.
    assert result.ok is False
    assert len(result.line_total_issues) == 1
    issue = result.line_total_issues[0]
    assert issue.line_index == 0
    assert issue.computed_line_total == Decimal("30.00")
    assert issue.stated_line_total == Decimal("35.00")
    assert issue.difference == Decimal("-5.00")


def test_one_cent_rounding_on_a_line_is_tolerated():
    # 3 * 0.335 = 1.005; invoice rounds the line to 1.01 — a rounding artefact,
    # not a discrepancy.
    result = check_invoice_totals(
        _invoice([_line("3", "0.335", "1.01")], "1.01")
    )

    assert result.line_total_issues == []
    assert result.ok is True


def test_mismatched_totals_fixture_flags_plus_81():
    payload = load_expected("mismatched_totals")
    invoice = _invoice(
        [
            _line(li["quantity"], li["unit_price"], li["line_total"], li["description"])
            for li in payload["line_items"]
        ],
        payload["grand_total"],
    )

    result = check_invoice_totals(invoice)

    assert result.ok is False
    assert result.computed_line_sum == Decimal("1690.00")
    assert result.stated_grand_total == Decimal("1609.00")
    assert result.difference == Decimal("81.00")
    assert result.line_total_issues == []


def test_clean_fixtures_reconcile():
    for slug in ("clean_office_supplies", "consulting_services"):
        payload = load_expected(slug)
        invoice = _invoice(
            [
                _line(li["quantity"], li["unit_price"], li["line_total"], li["description"])
                for li in payload["line_items"]
            ],
            payload["grand_total"],
        )
        assert check_invoice_totals(invoice).ok is True, slug
