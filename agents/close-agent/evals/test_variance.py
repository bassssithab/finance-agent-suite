import json
from decimal import Decimal

from connectors import BudgetActualLine

from close_agent import FlagThresholds, compute_variances


def _line(capability, account, line_item, amount, *, category="Operating expenses",
          period="2026-07", currency="USD"):
    return BudgetActualLine(
        source_system="test_co",
        source_capability=capability,
        period=period,
        account=account,
        line_item=line_item,
        category=category,
        amount=Decimal(amount),
        currency=currency,
        raw={},
    )


def _budget(account, line_item, amount, **kw):
    return _line("budget", account, line_item, amount, **kw)


def _actual(account, line_item, amount, **kw):
    return _line("actuals", account, line_item, amount, **kw)


def _compute(budget, actuals, thresholds=None):
    return compute_variances(
        budget, actuals, period="2026-07", currency="USD",
        thresholds=thresholds or FlagThresholds(),
    )


def test_variance_is_actual_minus_budget():
    [lv] = _compute(
        [_budget("6000", "Marketing", "40000.00")],
        [_actual("6000", "Marketing", "62000.00")],
    )
    assert lv.variance == Decimal("22000.00")
    assert lv.direction == "over_budget"
    assert lv.presence == "both"


def test_pct_variance_rounds_to_four_places():
    [lv] = _compute(
        [_budget("5000", "COGS", "900000.00")],
        [_actual("5000", "COGS", "940000.00")],
    )
    # 40000 / 900000 = 0.04444... -> quantized to 4 dp
    assert lv.pct_variance == Decimal("0.0444")


def test_zero_budget_gives_none_pct_and_actual_only_presence():
    [lv] = _compute(
        [],
        [_actual("6900", "Regulatory consulting", "8000.00")],
    )
    assert lv.budget_amount == Decimal("0")
    assert lv.pct_variance is None
    assert lv.presence == "actual_only"
    assert lv.flagged is True
    assert lv.flag_reasons == ["actual spend $8,000.00 against zero budget"]


def test_missing_actual_treated_as_zero_and_budget_only_presence():
    [lv] = _compute(
        [_budget("6300", "Software subscriptions", "18000.00")],
        [],
    )
    assert lv.actual_amount == Decimal("0")
    assert lv.variance == Decimal("-18000.00")
    assert lv.presence == "budget_only"
    assert "budgeted $18,000.00 with no actual reported" in lv.flag_reasons


def test_pct_threshold_flags_and_records_reason():
    [lv] = _compute(
        [_budget("6200", "Contract labour", "30000.00")],
        [_actual("6200", "Contract labour", "12000.00")],
    )
    assert lv.flagged is True
    assert lv.flag_reasons == [
        "absolute percentage variance 60.0% >= threshold 10.0%"
    ]


def test_amount_threshold_flags_independently_of_pct():
    thresholds = FlagThresholds(pct=None, amount=Decimal("25000"))
    [lv] = _compute(
        [_budget("5000", "COGS", "900000.00")],
        [_actual("5000", "COGS", "940000.00")],
        thresholds,
    )
    assert lv.flagged is True
    assert lv.flag_reasons == [
        "absolute variance $40,000.00 >= threshold $25,000.00"
    ]


def test_combine_all_requires_both_rules():
    thresholds = FlagThresholds(pct=Decimal("0.10"), amount=Decimal("25000"), combine="all")

    both = _compute(
        [_budget("6000", "Marketing", "100000.00")],
        [_actual("6000", "Marketing", "130000.00")],  # +30% and +30,000
        thresholds,
    )[0]
    assert both.flagged is True
    assert len(both.flag_reasons) == 2

    pct_only = _compute(
        [_budget("6000", "Marketing", "100.00")],
        [_actual("6000", "Marketing", "130.00")],  # +30% but only +30
        thresholds,
    )[0]
    assert pct_only.flagged is False


def test_presence_rules_can_be_disabled():
    thresholds = FlagThresholds(flag_unbudgeted=False, flag_missing_actual=False)

    unbudgeted = _compute([], [_actual("6500", "Bank fees", "1200.00")], thresholds)[0]
    assert unbudgeted.flagged is False

    missing = _compute([_budget("6300", "Subs", "18000.00")], [], thresholds)[0]
    # still flagged on the -100% pct rule, but with no presence reason
    assert missing.flag_reasons == [
        "absolute percentage variance 100.0% >= threshold 10.0%"
    ]


def test_on_budget_line_is_not_flagged():
    [lv] = _compute(
        [_budget("7000", "Rent", "12000.00")],
        [_actual("7000", "Rent", "12000.00")],
    )
    assert lv.direction == "on_budget"
    assert lv.flagged is False
    assert lv.pct_variance == Decimal("0.0000")


def test_output_sorted_by_account_then_line_item():
    budget = [
        _budget("6100", "Office supplies", "1.00"),
        _budget("6000", "Zebra spend", "1.00"),
        _budget("6000", "Alpha spend", "1.00"),
    ]
    result = _compute(budget, [])
    assert [(lv.account, lv.line_item) for lv in result] == [
        ("6000", "Alpha spend"),
        ("6000", "Zebra spend"),
        ("6100", "Office supplies"),
    ]


def test_to_dict_is_json_safe():
    [lv] = _compute(
        [_budget("6000", "Marketing", "40000.00")],
        [_actual("6000", "Marketing", "62000.00")],
    )
    payload = lv.to_dict()
    json.dumps(payload)  # must not raise
    assert payload["variance"] == "22000.00"
    assert payload["pct_variance"] == "0.5500"
