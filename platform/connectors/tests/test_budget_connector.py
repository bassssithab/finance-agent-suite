from decimal import Decimal
from pathlib import Path

import pytest

from connectors import BudgetActualLine, ConnectorParseError, FileBudgetActualConnector

FIXTURES = Path(__file__).parent / "fixtures"
BUDGET_FOLDER = FIXTURES / "budget"
ACTUALS_FOLDER = FIXTURES / "actuals"


def test_combined_fetch_reads_budget_and_actuals_csvs():
    connector = FileBudgetActualConnector(
        source_system="sample_co", budget_folder=BUDGET_FOLDER, actuals_folder=ACTUALS_FOLDER,
    )

    lines = connector.fetch_lines()

    assert all(isinstance(line, BudgetActualLine) for line in lines)
    assert sorted({line.source_capability for line in lines}) == ["actuals", "budget"]
    assert all(line.source_system == "sample_co" for line in lines)
    assert sum(1 for line in lines if line.source_capability == "budget") == 3


def test_row_normalization_and_optional_columns():
    connector = FileBudgetActualConnector(source_system="sample_co", budget_folder=BUDGET_FOLDER)

    lines = connector.fetch_lines()

    marketing = next(line for line in lines if line.account == "6000")
    assert marketing.period == "2026-07"
    assert marketing.line_item == "Marketing — paid media"
    assert marketing.category == "Operating expenses"
    assert marketing.amount == Decimal("40000.00")
    assert isinstance(marketing.amount, Decimal)
    assert marketing.currency == "USD"
    assert marketing.raw["account"] == "6000"

    # Office supplies budget row leaves `currency` blank -> defaults to USD.
    office = next(line for line in lines if line.account == "6100")
    assert office.currency == "USD"


def test_period_filter_is_exact_match():
    connector = FileBudgetActualConnector(
        source_system="sample_co", budget_folder=BUDGET_FOLDER, actuals_folder=ACTUALS_FOLDER,
    )

    lines = connector.fetch_lines(period="2026-07")

    assert lines
    assert {line.period for line in lines} == {"2026-07"}
    # The stray 2026-06 actuals row is filtered out.
    assert not any(line.period == "2026-06" for line in lines)


def test_results_sorted_deterministically():
    connector = FileBudgetActualConnector(
        source_system="sample_co", budget_folder=BUDGET_FOLDER, actuals_folder=ACTUALS_FOLDER,
    )

    lines = connector.fetch_lines()

    keys = [(line.source_capability, line.account, line.line_item) for line in lines]
    assert keys == sorted(keys)


def test_missing_folder_returns_empty_for_that_capability(tmp_path):
    connector = FileBudgetActualConnector(
        source_system="sample_co",
        budget_folder=BUDGET_FOLDER,
        actuals_folder=tmp_path / "no_such_folder",
    )

    lines = connector.fetch_lines()

    assert lines
    assert all(line.source_capability == "budget" for line in lines)


def test_malformed_amount_raises_connector_parse_error(tmp_path):
    bad_folder = tmp_path / "budget"
    bad_folder.mkdir()
    (bad_folder / "bad.csv").write_text(
        "period,account,line_item,category,amount,currency\n"
        "2026-07,6000,Marketing,Operating expenses,not-a-number,USD\n"
    )
    connector = FileBudgetActualConnector(source_system="sample_co", budget_folder=bad_folder)

    with pytest.raises(ConnectorParseError, match="bad.csv"):
        connector.fetch_lines()


def test_missing_required_column_raises_connector_parse_error(tmp_path):
    bad_folder = tmp_path / "budget"
    bad_folder.mkdir()
    (bad_folder / "bad.csv").write_text(
        "period,account,line_item,category,amount,currency\n"
        "2026-07,,Marketing,Operating expenses,100.00,USD\n"
    )
    connector = FileBudgetActualConnector(source_system="sample_co", budget_folder=bad_folder)

    with pytest.raises(ConnectorParseError, match="missing account"):
        connector.fetch_lines()
