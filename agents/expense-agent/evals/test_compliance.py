from datetime import timedelta
from decimal import Decimal

from expense_agent import ExpensePolicy, check_compliance
from expense_agent.models import ExtractedReceipt
from fixtures import AS_OF, record_receipt_payload

SAMPLE_POLICY = ExpensePolicy(
    category_limits={
        "meals": Decimal("75.00"),
        "travel - taxi": Decimal("60.00"),
        "lodging": Decimal("250.00"),
    },
    max_receipt_age_days=90,
)


def receipt(
    vendor="Larkspur City Cabs",
    date_str="2026-08-28",
    amount="38.40",
    currency="USD",
    category="Travel - taxi",
    confidence=0.95,
):
    return ExtractedReceipt(
        vendor=vendor,
        date=date_str,
        amount=Decimal(amount),
        currency=currency,
        expense_category=category,
        extraction_confidence=confidence,
    )


def codes(result):
    return sorted(v.code for v in result.violations)


def one(r, policy=SAMPLE_POLICY, as_of=AS_OF):
    return check_compliance(r, policy, as_of_date=as_of)


# --- clean path ----------------------------------------------------------


def test_clean_receipt_passes():
    result = one(receipt())
    assert result.passed is True
    assert result.violations == []
    assert result.parsed_date == "2026-08-28"
    assert result.applied_limit == Decimal("60.00")


# --- category spending limit -------------------------------------------


def test_amount_over_category_limit_is_flagged_with_exact_overage():
    result = one(receipt(amount="182.50", category="Meals"))
    assert codes(result) == ["category_over_limit"]
    detail = result.violations[0].detail
    assert "over by 107.50" in detail
    assert "75.00 limit" in detail
    assert result.violations[0].field == "amount"


def test_amount_exactly_at_the_limit_passes():
    assert one(receipt(amount="75.00", category="Meals")).passed is True
    assert one(receipt(amount="75.01", category="Meals")).passed is False


def test_default_limit_applies_to_an_unlisted_category():
    policy = ExpensePolicy(
        category_limits={"meals": Decimal("75")}, default_limit=Decimal("40")
    )
    over = one(receipt(amount="50.00", category="Software"), policy=policy)
    assert codes(over) == ["category_over_limit"]
    assert over.applied_limit == Decimal("40")


def test_unlisted_category_is_uncapped_when_no_default_limit():
    policy = ExpensePolicy(category_limits={"meals": Decimal("75")}, default_limit=None)
    result = one(receipt(amount="5000.00", category="Software"), policy=policy)
    assert result.passed is True
    assert result.applied_limit is None


def test_category_match_ignores_case_and_surrounding_whitespace():
    assert one(receipt(amount="100.00", category="  MEALS ")).violations[0].code == "category_over_limit"


# --- receipt age -------------------------------------------------------


def test_receipt_exactly_at_the_age_limit_passes_and_one_day_over_is_flagged():
    at_limit = receipt(date_str=(AS_OF - timedelta(days=90)).isoformat())
    assert one(at_limit).passed is True

    over = receipt(date_str=(AS_OF - timedelta(days=91)).isoformat())
    result = one(over)
    assert codes(result) == ["receipt_too_old"]
    assert "91 days old" in result.violations[0].detail
    assert "90-day limit" in result.violations[0].detail
    assert AS_OF.isoformat() in result.violations[0].detail


def test_no_max_age_means_the_age_is_never_checked():
    policy = ExpensePolicy(category_limits=SAMPLE_POLICY.category_limits, max_receipt_age_days=None)
    ancient = receipt(date_str="2019-01-01")
    assert one(ancient, policy=policy).passed is True


def test_non_iso_date_parses_with_a_configured_format():
    policy = ExpensePolicy(
        category_limits=SAMPLE_POLICY.category_limits,
        max_receipt_age_days=90,
        date_formats=("%d/%m/%Y",),
    )
    result = one(receipt(date_str="03/04/2026"), policy=policy)  # 2026-04-03
    assert result.parsed_date == "2026-04-03"
    assert codes(result) == ["receipt_too_old"]


def test_unparseable_date_is_flagged_and_skips_the_age_check():
    result = one(receipt(date_str="last Tuesday", amount="182.50", category="Meals"))
    assert "date_unparseable" in codes(result)
    assert "receipt_too_old" not in codes(result)  # age check skipped, no false positive
    assert "category_over_limit" in codes(result)  # other checks still run
    assert result.parsed_date is None


# --- required fields --------------------------------------------------


def test_missing_required_field_is_flagged_per_field():
    result = one(receipt(vendor="", currency=""))
    assert codes(result) == ["missing_required_field", "missing_required_field"]
    assert sorted(v.field for v in result.violations) == ["currency", "vendor"]


def test_blank_amount_counts_as_missing():
    result = one(receipt(amount="0"))
    missing = [v for v in result.violations if v.code == "missing_required_field"]
    assert [v.field for v in missing] == ["amount"]


def test_one_receipt_can_carry_several_distinct_violations():
    result = one(receipt(vendor="", amount="500.00", category="Meals", date_str="2026-01-01"))
    assert codes(result) == ["category_over_limit", "missing_required_field", "receipt_too_old"]
    assert result.passed is False


# --- the committed fixtures ------------------------------------------


def _from_payload(slug):
    p = record_receipt_payload(slug)
    return ExtractedReceipt(
        vendor=p["vendor"],
        date=p["date"],
        amount=Decimal(p["amount"]),
        currency=p["currency"],
        expense_category=p["expense_category"],
        extraction_confidence=p["extraction_confidence"],
    )


def test_committed_fixtures_produce_the_expected_verdicts():
    assert one(_from_payload("compliant_taxi")).passed is True
    assert codes(one(_from_payload("over_limit_dinner"))) == ["category_over_limit"]
    assert codes(one(_from_payload("stale_hotel"))) == ["receipt_too_old"]
