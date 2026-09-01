from decimal import Decimal
from pathlib import Path

import pytest
from connectors import BudgetActualLine, FileBudgetActualConnector

from fpa_agent import ForecastAssumptions, project_forecast

ACTUALS_DIR = Path(__file__).parent / "fixtures" / "actuals"


def line(account="4000", line_item="Product revenue", category="Revenue", period="2026-06", amount="100.00"):
    return BudgetActualLine(
        source_system="test_co",
        source_capability="actuals",
        period=period,
        account=account,
        line_item=line_item,
        category=category,
        amount=Decimal(amount),
        currency="USD",
        raw={},
    )


def fixture_actuals():
    return FileBudgetActualConnector(source_system="larenthia_trading", actuals_folder=ACTUALS_DIR).fetch_lines()


def project(actuals, *, assumptions=None, horizon=3):
    return project_forecast(
        actuals,
        assumptions=assumptions or ForecastAssumptions(),
        horizon=horizon,
        currency="USD",
    )


# --- calendar --------------------------------------------------------------


def test_advance_across_year_boundary():
    _, base_period, periods = project([line(period="2026-11")], horizon=3)
    assert base_period == "2026-11"
    assert periods == ["2026-12", "2027-01", "2027-02"]


def test_non_monthly_period_is_rejected():
    with pytest.raises(ValueError, match="YYYY-MM"):
        project([line(period="2026-Q1")])


def test_horizon_must_be_positive():
    with pytest.raises(ValueError, match="horizon"):
        project([line()], horizon=0)


def test_no_actuals_is_rejected():
    with pytest.raises(ValueError, match="no historical actuals"):
        project([])


# --- base value -----------------------------------------------------------


def test_base_period_is_the_most_recent_present():
    lines, base_period, _ = project(fixture_actuals())
    assert base_period == "2026-06"


def test_base_value_is_taken_from_the_base_period():
    lines, _, _ = project(fixture_actuals())
    revenue_k1 = next(pl for pl in lines if pl.account == "4000" and pl.period_index == 1)
    assert revenue_k1.base_period == "2026-06"
    assert revenue_k1.base_amount == Decimal("851000.00")


def test_line_absent_from_base_period_is_carried_forward_and_flagged():
    lines = [pl for pl in project(fixture_actuals())[0] if pl.account == "6300"]
    assert lines, "6300 should still be projected"
    for pl in lines:
        assert pl.base_period == "2026-05"           # carried forward
        assert pl.base_amount == Decimal("9700.00")
        assert pl.flagged is True
        assert any("carried forward from 2026-05" in r for r in pl.flag_reasons)


def test_pct_change_is_none_when_base_is_zero():
    lines, _, _ = project([line(amount="0.00")])
    assert all(pl.pct_change_vs_base is None for pl in lines)


# --- growth --------------------------------------------------------------


def test_growth_is_compounded_over_the_horizon():
    lines, _, _ = project(
        [line(amount="100.00")],
        assumptions=ForecastAssumptions(default_growth=Decimal("0.10")),
        horizon=3,
    )
    assert [pl.projected_amount for pl in lines] == [
        Decimal("110.00"), Decimal("121.00"), Decimal("133.10")
    ]
    assert lines[0].change_vs_base == Decimal("10.00")


def test_category_rate_overrides_the_default_and_records_the_source():
    actuals = [line(account="4000", category="Revenue"), line(account="5000", line_item="COGS", category="Cost of sales")]
    lines, _, _ = project(
        actuals,
        assumptions=ForecastAssumptions(
            default_growth=Decimal("0.02"),
            category_growth={"Revenue": Decimal("0.05")},
        ),
    )
    rev = next(pl for pl in lines if pl.account == "4000")
    cogs = next(pl for pl in lines if pl.account == "5000")
    assert rev.growth_rate == Decimal("0.05") and rev.growth_source == "category"
    assert cogs.growth_rate == Decimal("0.02") and cogs.growth_source == "default"


# --- flagging ----------------------------------------------------------


def test_high_sensitivity_flag_fires_at_or_above_the_threshold():
    at = project([line()], assumptions=ForecastAssumptions(default_growth=Decimal("0.25")))[0]
    assert all(pl.flagged for pl in at)
    assert all(any("sensitivity threshold" in r for r in pl.flag_reasons) for pl in at)

    below = project([line()], assumptions=ForecastAssumptions(default_growth=Decimal("0.2499")))[0]
    assert all(not pl.flagged for pl in below)


def test_negative_projection_is_flagged_and_can_be_disabled():
    on = project(
        [line(amount="100.00")],
        assumptions=ForecastAssumptions(default_growth=Decimal("-1.5")),
        horizon=2,
    )[0]
    # (1 + -1.5) = -0.5 -> period 1 negative, period 2 positive
    assert on[0].projected_amount < 0 and on[0].flagged
    assert any("below zero" in r for r in on[0].flag_reasons)

    off = project(
        [line(amount="100.00")],
        assumptions=ForecastAssumptions(default_growth=Decimal("-1.5"), flag_negative=False),
        horizon=2,
    )[0]
    assert not any("below zero" in r for pl in off for r in pl.flag_reasons)
    # -1.5 is still >= the 0.25 sensitivity threshold, so the line is flagged for that
    assert off[0].flagged and any("sensitivity threshold" in r for r in off[0].flag_reasons)


# --- ordering ---------------------------------------------------------


def test_output_sorted_by_account_line_item_period_index():
    lines, _, _ = project(fixture_actuals())
    keys = [(pl.account, pl.line_item, pl.period_index) for pl in lines]
    assert keys == sorted(keys)


def test_one_row_per_line_per_future_period():
    lines, _, periods = project(fixture_actuals(), horizon=3)
    distinct_lines = {(pl.account, pl.line_item) for pl in lines}
    assert len(lines) == len(distinct_lines) * len(periods) == 6 * 3
