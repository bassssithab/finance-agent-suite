from datetime import date, timedelta
from decimal import Decimal

from connectors import OpenInvoice

from ar_collections_agent import DunningPolicy, compute_aging

AS_OF = date(2026, 9, 1)
DEFAULT_POLICY = DunningPolicy()


def inv(
    invoice_id="INV-1",
    customer="Acme",
    days_overdue=0,
    amount="1000.00",
    last_payment_date=None,
):
    due = AS_OF - timedelta(days=days_overdue)
    return OpenInvoice(
        source_system="test_co",
        source_capability="open_invoices",
        invoice_id=invoice_id,
        customer=customer,
        invoice_date=due - timedelta(days=30),
        due_date=due,
        amount=Decimal(amount),
        currency="USD",
        last_payment_date=last_payment_date,
        raw={},
    )


def one(invoice, policy=DEFAULT_POLICY):
    (result,) = compute_aging([invoice], as_of_date=AS_OF, policy=policy)
    return result


# --- buckets --------------------------------------------------------------


def test_bucket_boundaries():
    cases = {
        -5: "current",
        0: "current",
        1: "1-30",
        30: "1-30",
        31: "31-60",
        60: "31-60",
        61: "61-90",
        90: "61-90",
        91: "90+",
        400: "90+",
    }
    for days, bucket in cases.items():
        assert one(inv(days_overdue=days)).bucket == bucket, days


def test_days_overdue_is_signed():
    assert one(inv(days_overdue=-9)).days_overdue == -9
    assert one(inv(days_overdue=45)).days_overdue == 45


# --- flagging: single-invoice days-overdue rule ---------------------------


def test_invoice_at_min_days_overdue_is_flagged_with_reason():
    result = one(inv(days_overdue=31))
    assert result.flagged is True
    assert result.flag_reasons == ["31 days overdue >= threshold 31"]


def test_invoice_below_min_days_overdue_is_not_flagged():
    result = one(inv(days_overdue=30))
    assert result.flagged is False
    assert result.flag_reasons == []
    assert result.tone_tier is None


def test_min_days_overdue_is_configurable():
    lenient = DunningPolicy(min_days_overdue=60, flag_repeat_customers=False)
    assert one(inv(days_overdue=45), lenient).flagged is False
    assert one(inv(days_overdue=60), lenient).flagged is True


# --- flagging: repeat-customer rule --------------------------------------


def test_repeat_customer_pulls_in_a_low_age_overdue_invoice():
    invoices = [
        inv("INV-A", customer="Halvar", days_overdue=10),   # 1-30, below min_days
        inv("INV-B", customer="Halvar", days_overdue=40),   # 31-60, over min_days
        inv("INV-C", customer="Solo", days_overdue=12),     # only one for Solo
    ]
    by_id = {ia.invoice_id: ia for ia in compute_aging(invoices, as_of_date=AS_OF, policy=DEFAULT_POLICY)}

    assert by_id["INV-A"].flagged is True
    assert by_id["INV-A"].flag_reasons == [
        "customer 'Halvar' has 2 overdue invoices (>= threshold 2)"
    ]
    assert by_id["INV-A"].tone_tier == "reminder"
    # INV-B trips both rules
    assert by_id["INV-B"].flag_reasons == [
        "40 days overdue >= threshold 31",
        "customer 'Halvar' has 2 overdue invoices (>= threshold 2)",
    ]
    # Solo has only one overdue invoice, below the age threshold -> not flagged
    assert by_id["INV-C"].flagged is False


def test_repeat_customer_rule_needs_invoices_actually_overdue():
    invoices = [
        inv("INV-A", customer="Halvar", days_overdue=0),    # not overdue
        inv("INV-B", customer="Halvar", days_overdue=10),   # only one overdue
    ]
    by_id = {ia.invoice_id: ia for ia in compute_aging(invoices, as_of_date=AS_OF, policy=DEFAULT_POLICY)}
    assert by_id["INV-A"].flagged is False
    assert by_id["INV-B"].flagged is False


def test_repeat_customer_rule_can_be_disabled():
    policy = DunningPolicy(flag_repeat_customers=False)
    invoices = [
        inv("INV-A", customer="Halvar", days_overdue=10),
        inv("INV-B", customer="Halvar", days_overdue=12),
    ]
    results = compute_aging(invoices, as_of_date=AS_OF, policy=policy)
    assert all(ia.flagged is False for ia in results)


# --- flagging: minimum-balance suppression -------------------------------


def test_min_amount_suppresses_a_small_overdue_balance():
    policy = DunningPolicy(min_amount=Decimal("500"))
    assert one(inv(days_overdue=120, amount="499.99"), policy).flagged is False
    assert one(inv(days_overdue=120, amount="500.00"), policy).flagged is True


# --- tone tiers ---------------------------------------------------------


def test_tone_tier_escalates_with_age():
    assert one(inv(days_overdue=45)).tone_tier == "reminder"
    assert one(inv(days_overdue=60)).tone_tier == "reminder"
    assert one(inv(days_overdue=61)).tone_tier == "firm"
    assert one(inv(days_overdue=90)).tone_tier == "firm"
    assert one(inv(days_overdue=91)).tone_tier == "formal"


# --- misc -------------------------------------------------------------


def test_days_since_last_payment():
    result = one(inv(days_overdue=40, last_payment_date=date(2026, 7, 15)))
    assert result.days_since_last_payment == (AS_OF - date(2026, 7, 15)).days
    assert one(inv(days_overdue=40)).days_since_last_payment is None


def test_output_sorted_by_customer_then_due_date_then_id():
    invoices = [
        inv("INV-3", customer="Zeta", days_overdue=5),
        inv("INV-1", customer="Alpha", days_overdue=40),
        inv("INV-2", customer="Alpha", days_overdue=10),
    ]
    results = compute_aging(invoices, as_of_date=AS_OF, policy=DEFAULT_POLICY)
    # Alpha sorts before Zeta; within Alpha the earlier due date (INV-1) comes first.
    assert [ia.invoice_id for ia in results] == ["INV-1", "INV-2", "INV-3"]


def test_dates_are_serialized_as_iso_strings():
    result = one(inv(days_overdue=40))
    assert result.due_date == (AS_OF - timedelta(days=40)).isoformat()
    assert isinstance(result.amount, Decimal)
