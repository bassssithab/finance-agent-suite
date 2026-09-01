from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from connectors import ConnectorParseError, FileVatTransactionConnector, VatTransaction

FIXTURES = Path(__file__).parent / "fixtures"
VAT_FOLDER = FIXTURES / "vat_transactions"


def test_fetch_normalizes_rows_into_vat_transactions():
    connector = FileVatTransactionConnector(source_system="sample_co", folder=VAT_FOLDER)

    txns = connector.fetch_transactions()

    assert all(isinstance(t, VatTransaction) for t in txns)
    assert all(t.source_system == "sample_co" for t in txns)
    assert all(t.source_capability == "vat_transactions" for t in txns)

    sale = next(t for t in txns if t.transaction_id == "TXN-1001")
    assert sale.date == date(2026, 7, 2)
    assert sale.transaction_type == "sale"
    assert sale.amount == Decimal("88000.00")
    assert isinstance(sale.amount, Decimal)
    assert sale.vat_treatment == "standard-rated"
    assert sale.vat_rate == Decimal("0.15")
    assert sale.currency == "USD"
    assert sale.raw["transaction_id"] == "TXN-1001"


def test_blank_vat_rate_becomes_none_not_zero():
    connector = FileVatTransactionConnector(source_system="sample_co", folder=VAT_FOLDER)
    txns = {t.transaction_id: t for t in connector.fetch_transactions()}

    # TXN-1004 leaves vat_rate and currency blank.
    assert txns["TXN-1004"].vat_rate is None
    assert txns["TXN-1004"].currency == "USD"
    assert txns["TXN-1004"].vat_treatment == "zero-rated"
    # TXN-1001 records a rate.
    assert txns["TXN-1001"].vat_rate == Decimal("0.15")


def test_results_sorted_by_date_then_transaction_id():
    connector = FileVatTransactionConnector(source_system="sample_co", folder=VAT_FOLDER)
    txns = connector.fetch_transactions()

    keys = [(t.date, t.transaction_id) for t in txns]
    assert keys == sorted(keys)
    assert [t.transaction_id for t in txns] == [
        "TXN-1001", "TXN-1002", "TXN-1003", "TXN-1004"
    ]


def test_multiple_csvs_in_folder_are_all_read(tmp_path):
    folder = tmp_path / "vat_transactions"
    folder.mkdir()
    (folder / "batch_a.csv").write_text(
        "transaction_id,date,transaction_type,amount,vat_treatment,vat_rate\n"
        "TXN-1,2026-08-01,sale,100.00,standard-rated,0.15\n"
    )
    (folder / "batch_b.csv").write_text(
        "transaction_id,date,transaction_type,amount,vat_treatment,vat_rate\n"
        "TXN-2,2026-08-02,purchase,200.00,exempt,\n"
    )
    connector = FileVatTransactionConnector(source_system="sample_co", folder=folder)

    assert [t.transaction_id for t in connector.fetch_transactions()] == ["TXN-1", "TXN-2"]


def test_missing_folder_returns_empty(tmp_path):
    connector = FileVatTransactionConnector(
        source_system="sample_co", folder=tmp_path / "no_such_folder"
    )
    assert connector.fetch_transactions() == []


def test_blank_treatment_is_kept_as_empty_string_not_rejected(tmp_path):
    folder = tmp_path / "vat_transactions"
    folder.mkdir()
    (folder / "t.csv").write_text(
        "transaction_id,date,transaction_type,amount,vat_treatment,vat_rate\n"
        "TXN-9,2026-07-01,sale,100.00,,\n"
    )
    connector = FileVatTransactionConnector(source_system="sample_co", folder=folder)

    (txn,) = connector.fetch_transactions()
    assert txn.vat_treatment == ""  # the agent flags this, not the connector


def test_missing_required_value_raises_connector_parse_error(tmp_path):
    folder = tmp_path / "vat_transactions"
    folder.mkdir()
    (folder / "bad.csv").write_text(
        "transaction_id,date,transaction_type,amount,vat_treatment,vat_rate\n"
        "TXN-9,2026-07-01,,100.00,standard-rated,0.15\n"
    )
    connector = FileVatTransactionConnector(source_system="sample_co", folder=folder)

    with pytest.raises(ConnectorParseError, match="missing transaction_type"):
        connector.fetch_transactions()


def test_malformed_amount_raises_connector_parse_error(tmp_path):
    folder = tmp_path / "vat_transactions"
    folder.mkdir()
    (folder / "bad.csv").write_text(
        "transaction_id,date,transaction_type,amount,vat_treatment,vat_rate\n"
        "TXN-9,2026-07-01,sale,not-a-number,standard-rated,0.15\n"
    )
    connector = FileVatTransactionConnector(source_system="sample_co", folder=folder)

    with pytest.raises(ConnectorParseError, match="bad.csv"):
        connector.fetch_transactions()


def test_malformed_rate_raises_connector_parse_error(tmp_path):
    folder = tmp_path / "vat_transactions"
    folder.mkdir()
    (folder / "bad.csv").write_text(
        "transaction_id,date,transaction_type,amount,vat_treatment,vat_rate\n"
        "TXN-9,2026-07-01,sale,100.00,standard-rated,fifteen-percent\n"
    )
    connector = FileVatTransactionConnector(source_system="sample_co", folder=folder)

    with pytest.raises(ConnectorParseError, match="vat_rate"):
        connector.fetch_transactions()


def test_malformed_date_raises_connector_parse_error(tmp_path):
    folder = tmp_path / "vat_transactions"
    folder.mkdir()
    (folder / "bad.csv").write_text(
        "transaction_id,date,transaction_type,amount,vat_treatment,vat_rate\n"
        "TXN-9,07/01/2026,sale,100.00,standard-rated,0.15\n"
    )
    connector = FileVatTransactionConnector(source_system="sample_co", folder=folder)

    with pytest.raises(ConnectorParseError, match="invalid date"):
        connector.fetch_transactions()
