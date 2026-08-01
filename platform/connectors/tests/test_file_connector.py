from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from connectors import ConnectorParseError, FileConnector

FIXTURES = Path(__file__).parent / "fixtures"
BANK_FOLDER = FIXTURES / "bank"
LEDGER_FOLDER = FIXTURES / "ledger"


def test_combined_fetch_reads_bank_and_ledger_csvs():
    connector = FileConnector(
        source_system="sample_co", bank_folder=BANK_FOLDER, ledger_folder=LEDGER_FOLDER,
    )

    transactions = connector.fetch_transactions()

    assert len(transactions) == 6
    assert sum(1 for t in transactions if t.source_capability == "bank") == 3
    assert sum(1 for t in transactions if t.source_capability == "erp") == 3
    assert all(t.source_system == "sample_co" for t in transactions)


def test_bank_row_normalization():
    connector = FileConnector(source_system="sample_co", bank_folder=BANK_FOLDER)

    transactions = connector.fetch_transactions()

    deposit = next(t for t in transactions if t.reference == "REF001")
    assert deposit.account == "chase-1234"
    assert deposit.date == date(2026, 7, 1)
    assert deposit.amount == Decimal("1000.00")
    assert isinstance(deposit.amount, Decimal)
    assert deposit.currency == "USD"
    assert deposit.description == "Opening deposit"
    assert deposit.raw["reference"] == "REF001"

    withdrawal = next(t for t in transactions if t.reference == "REF002")
    assert withdrawal.amount == Decimal("-42.50")


def test_ledger_row_normalization_nets_debit_credit():
    connector = FileConnector(source_system="sample_co", ledger_folder=LEDGER_FOLDER)

    transactions = connector.fetch_transactions()

    debit_line = next(t for t in transactions if t.reference == "LREF001")
    assert debit_line.amount == Decimal("1000.00")

    credit_line = next(t for t in transactions if t.reference == "LREF003")
    assert credit_line.amount == Decimal("-150.00")
    assert credit_line.account == "1000-Cash"
    assert credit_line.description == "Office supplies payment"


def test_date_range_filtering():
    connector = FileConnector(source_system="sample_co", bank_folder=BANK_FOLDER)

    transactions = connector.fetch_transactions(
        start_date=date(2026, 7, 2), end_date=date(2026, 7, 31),
    )

    assert len(transactions) == 1
    assert transactions[0].reference == "REF002"


def test_results_sorted_deterministically():
    connector = FileConnector(source_system="sample_co", ledger_folder=LEDGER_FOLDER)

    transactions = connector.fetch_transactions()

    dates_and_accounts = [(t.date, t.account) for t in transactions]
    assert dates_and_accounts == sorted(dates_and_accounts)


def test_missing_folder_returns_empty_for_that_capability(tmp_path):
    connector = FileConnector(
        source_system="sample_co", bank_folder=BANK_FOLDER, ledger_folder=tmp_path / "no_such_folder",
    )

    transactions = connector.fetch_transactions()

    assert transactions
    assert all(t.source_capability == "bank" for t in transactions)


def test_malformed_row_raises_connector_parse_error(tmp_path):
    bad_folder = tmp_path / "bank"
    bad_folder.mkdir()
    (bad_folder / "bad.csv").write_text(
        "date,account,description,amount,balance,reference\n"
        "2026-07-01,chase-1234,Bad row,not-a-number,0,REF999\n"
    )
    connector = FileConnector(source_system="sample_co", bank_folder=bad_folder)

    with pytest.raises(ConnectorParseError, match="bad.csv"):
        connector.fetch_transactions()
