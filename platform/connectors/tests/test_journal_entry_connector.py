from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from connectors import ConnectorParseError, FileJournalEntryConnector, JournalEntry

FIXTURES = Path(__file__).parent / "fixtures"
JE_FOLDER = FIXTURES / "journal_entries"


def test_fetch_normalizes_rows_into_journal_entries():
    connector = FileJournalEntryConnector(source_system="sample_co", folder=JE_FOLDER)

    entries = connector.fetch_entries()

    assert all(isinstance(e, JournalEntry) for e in entries)
    assert all(e.source_system == "sample_co" for e in entries)
    assert all(e.source_capability == "journal_entries" for e in entries)

    dual = next(e for e in entries if e.entry_id == "JE-1001")
    assert dual.date == date(2026, 7, 2)
    assert dual.account == "4000"
    assert dual.amount == Decimal("88000.00")
    assert isinstance(dual.amount, Decimal)
    assert dual.currency == "USD"
    assert dual.preparer == "dpatel"
    assert dual.approver_1 == "rmorgan"
    assert dual.approver_2 == "tokafor"
    assert dual.raw["entry_id"] == "JE-1001"


def test_blank_approvers_and_currency_become_defaults():
    connector = FileJournalEntryConnector(source_system="sample_co", folder=JE_FOLDER)

    entries = {e.entry_id: e for e in connector.fetch_entries()}

    # JE-1002 leaves approver_2 blank.
    assert entries["JE-1002"].approver_1 == "rmorgan"
    assert entries["JE-1002"].approver_2 is None

    # JE-1004 leaves both approvers and currency blank.
    assert entries["JE-1004"].approver_1 is None
    assert entries["JE-1004"].approver_2 is None
    assert entries["JE-1004"].currency == "USD"


def test_results_sorted_by_date_then_entry_id():
    connector = FileJournalEntryConnector(source_system="sample_co", folder=JE_FOLDER)

    entries = connector.fetch_entries()

    keys = [(e.date, e.entry_id) for e in entries]
    assert keys == sorted(keys)
    assert [e.entry_id for e in entries] == ["JE-1001", "JE-1002", "JE-1003", "JE-1004"]


def test_multiple_csvs_in_folder_are_all_read(tmp_path):
    folder = tmp_path / "journal_entries"
    folder.mkdir()
    (folder / "batch_a.csv").write_text(
        "entry_id,date,account,amount,preparer,approver_1,approver_2\n"
        "JE-1,2026-08-01,6000,100.00,alice,bob,\n"
    )
    (folder / "batch_b.csv").write_text(
        "entry_id,date,account,amount,preparer,approver_1,approver_2\n"
        "JE-2,2026-08-02,6000,200.00,carol,dave,\n"
    )
    connector = FileJournalEntryConnector(source_system="sample_co", folder=folder)

    assert [e.entry_id for e in connector.fetch_entries()] == ["JE-1", "JE-2"]


def test_missing_folder_returns_empty(tmp_path):
    connector = FileJournalEntryConnector(
        source_system="sample_co", folder=tmp_path / "no_such_folder"
    )
    assert connector.fetch_entries() == []


def test_missing_required_value_raises_connector_parse_error(tmp_path):
    folder = tmp_path / "journal_entries"
    folder.mkdir()
    (folder / "bad.csv").write_text(
        "entry_id,date,account,amount,preparer,approver_1,approver_2\n"
        "JE-9,2026-07-01,6000,100.00,,bob,\n"
    )
    connector = FileJournalEntryConnector(source_system="sample_co", folder=folder)

    with pytest.raises(ConnectorParseError, match="missing preparer"):
        connector.fetch_entries()


def test_malformed_amount_raises_connector_parse_error(tmp_path):
    folder = tmp_path / "journal_entries"
    folder.mkdir()
    (folder / "bad.csv").write_text(
        "entry_id,date,account,amount,preparer,approver_1,approver_2\n"
        "JE-9,2026-07-01,6000,not-a-number,alice,bob,\n"
    )
    connector = FileJournalEntryConnector(source_system="sample_co", folder=folder)

    with pytest.raises(ConnectorParseError, match="bad.csv"):
        connector.fetch_entries()


def test_malformed_date_raises_connector_parse_error(tmp_path):
    folder = tmp_path / "journal_entries"
    folder.mkdir()
    (folder / "bad.csv").write_text(
        "entry_id,date,account,amount,preparer,approver_1,approver_2\n"
        "JE-9,07/01/2026,6000,100.00,alice,bob,\n"
    )
    connector = FileJournalEntryConnector(source_system="sample_co", folder=folder)

    with pytest.raises(ConnectorParseError, match="invalid date"):
        connector.fetch_entries()
