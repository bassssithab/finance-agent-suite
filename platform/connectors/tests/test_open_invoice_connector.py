from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from connectors import ConnectorParseError, FileOpenInvoiceConnector, OpenInvoice

FIXTURES = Path(__file__).parent / "fixtures"
INVOICES_FOLDER = FIXTURES / "open_invoices"


def test_fetch_normalizes_rows_into_open_invoices():
    connector = FileOpenInvoiceConnector(source_system="sample_co", folder=INVOICES_FOLDER)

    invoices = connector.fetch_invoices()

    assert all(isinstance(inv, OpenInvoice) for inv in invoices)
    assert all(inv.source_system == "sample_co" for inv in invoices)
    assert all(inv.source_capability == "open_invoices" for inv in invoices)

    big = next(inv for inv in invoices if inv.invoice_id == "INV-2001")
    assert big.customer == "Aldwe Retail"
    assert big.invoice_date == date(2026, 7, 1)
    assert big.due_date == date(2026, 7, 31)
    assert big.amount == Decimal("88000.00")
    assert isinstance(big.amount, Decimal)
    assert big.currency == "USD"
    assert big.last_payment_date is None
    assert big.raw["invoice_id"] == "INV-2001"


def test_blank_currency_and_last_payment_become_defaults():
    connector = FileOpenInvoiceConnector(source_system="sample_co", folder=INVOICES_FOLDER)

    invoices = {inv.invoice_id: inv for inv in connector.fetch_invoices()}

    # INV-2004 leaves currency and last_payment_date blank.
    assert invoices["INV-2004"].currency == "USD"
    assert invoices["INV-2004"].last_payment_date is None
    # INV-2002 records a last payment date.
    assert invoices["INV-2002"].last_payment_date == date(2026, 5, 30)


def test_results_sorted_by_due_date_then_invoice_id():
    connector = FileOpenInvoiceConnector(source_system="sample_co", folder=INVOICES_FOLDER)

    invoices = connector.fetch_invoices()

    keys = [(inv.due_date, inv.invoice_id) for inv in invoices]
    assert keys == sorted(keys)
    assert [inv.invoice_id for inv in invoices] == [
        "INV-2004", "INV-2003", "INV-2002", "INV-2001"
    ]


def test_multiple_csvs_in_folder_are_all_read(tmp_path):
    folder = tmp_path / "open_invoices"
    folder.mkdir()
    (folder / "batch_a.csv").write_text(
        "invoice_id,customer,invoice_date,due_date,amount,currency,last_payment_date\n"
        "INV-1,Acme,2026-08-01,2026-08-31,100.00,USD,\n"
    )
    (folder / "batch_b.csv").write_text(
        "invoice_id,customer,invoice_date,due_date,amount,currency,last_payment_date\n"
        "INV-2,Beta,2026-08-02,2026-09-01,200.00,USD,\n"
    )
    connector = FileOpenInvoiceConnector(source_system="sample_co", folder=folder)

    assert [inv.invoice_id for inv in connector.fetch_invoices()] == ["INV-1", "INV-2"]


def test_missing_folder_returns_empty(tmp_path):
    connector = FileOpenInvoiceConnector(
        source_system="sample_co", folder=tmp_path / "no_such_folder"
    )
    assert connector.fetch_invoices() == []


def test_missing_required_value_raises_connector_parse_error(tmp_path):
    folder = tmp_path / "open_invoices"
    folder.mkdir()
    (folder / "bad.csv").write_text(
        "invoice_id,customer,invoice_date,due_date,amount,currency,last_payment_date\n"
        "INV-9,,2026-07-01,2026-07-31,100.00,USD,\n"
    )
    connector = FileOpenInvoiceConnector(source_system="sample_co", folder=folder)

    with pytest.raises(ConnectorParseError, match="missing customer"):
        connector.fetch_invoices()


def test_malformed_amount_raises_connector_parse_error(tmp_path):
    folder = tmp_path / "open_invoices"
    folder.mkdir()
    (folder / "bad.csv").write_text(
        "invoice_id,customer,invoice_date,due_date,amount,currency,last_payment_date\n"
        "INV-9,Acme,2026-07-01,2026-07-31,not-a-number,USD,\n"
    )
    connector = FileOpenInvoiceConnector(source_system="sample_co", folder=folder)

    with pytest.raises(ConnectorParseError, match="bad.csv"):
        connector.fetch_invoices()


def test_malformed_due_date_raises_connector_parse_error(tmp_path):
    folder = tmp_path / "open_invoices"
    folder.mkdir()
    (folder / "bad.csv").write_text(
        "invoice_id,customer,invoice_date,due_date,amount,currency,last_payment_date\n"
        "INV-9,Acme,2026-07-01,07/31/2026,100.00,USD,\n"
    )
    connector = FileOpenInvoiceConnector(source_system="sample_co", folder=folder)

    with pytest.raises(ConnectorParseError, match="invalid date"):
        connector.fetch_invoices()


def test_malformed_last_payment_date_raises_connector_parse_error(tmp_path):
    folder = tmp_path / "open_invoices"
    folder.mkdir()
    (folder / "bad.csv").write_text(
        "invoice_id,customer,invoice_date,due_date,amount,currency,last_payment_date\n"
        "INV-9,Acme,2026-07-01,2026-07-31,100.00,USD,not-a-date\n"
    )
    connector = FileOpenInvoiceConnector(source_system="sample_co", folder=folder)

    with pytest.raises(ConnectorParseError, match="invalid date"):
        connector.fetch_invoices()
