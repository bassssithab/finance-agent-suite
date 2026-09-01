"""Local-folder stand-in for a real accounts-receivable / AR sub-ledger export
connector.

Reads a folder of open-invoice CSVs and normalizes each row into an
`OpenInvoice` record, until Phase 1 builds a connector against a live ERP AR
API. Same read-first contract as the other connectors: there are no write
methods here — an agent never writes off, dunning-flags, or edits an invoice in
the source, it only reads the open invoices and drafts something a human
approves (CLAUDE.md golden rules #1 and #2).

Expected CSV schema:
    invoice_id,customer,invoice_date,due_date,amount,currency,last_payment_date

`currency` and `last_payment_date` are optional — a blank or missing `currency`
becomes "USD", a blank or missing `last_payment_date` becomes `None` (the aging
analysis, not this connector, decides what to make of a customer who has never
paid). `invoice_id`, `customer`, `invoice_date`, `due_date` and `amount` are
required — a blank one raises `ConnectorParseError`.
"""

from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from typing import Optional, Union

from .base import ConnectorParseError
from .file_connector import _parse_date, _parse_decimal, _read_rows
from .models import OpenInvoice

SOURCE_CAPABILITY = "open_invoices"


class OpenInvoiceConnector(ABC):
    """Read-only interface for fetching open accounts-receivable invoices.

    Separate from `Connector` (which returns `Transaction` records built from
    debit/credit legs), `BudgetActualConnector`, `DocumentConnector` and
    `JournalEntryConnector`: this one deals in open AR invoices with their
    customer and due-date metadata.
    """

    @abstractmethod
    def fetch_invoices(self) -> list[OpenInvoice]:
        """Return every open invoice, normalized and deterministically sorted."""
        raise NotImplementedError


class FileOpenInvoiceConnector(OpenInvoiceConnector):
    def __init__(self, source_system: str, folder: Optional[Union[str, Path]] = None):
        self.source_system = source_system
        self.folder = Path(folder) if folder is not None else None

    def fetch_invoices(self) -> list[OpenInvoice]:
        invoices: list[OpenInvoice] = []

        if self.folder is not None and self.folder.is_dir():
            for csv_path in sorted(self.folder.glob("*.csv")):
                invoices.extend(self._parse_file(csv_path))

        invoices.sort(key=lambda inv: (inv.due_date, inv.invoice_id))
        return invoices

    def _parse_file(self, csv_path: Path) -> list[OpenInvoice]:
        results: list[OpenInvoice] = []
        for row_number, row in enumerate(_read_rows(csv_path), start=2):
            for required in ("invoice_id", "customer", "invoice_date", "due_date", "amount"):
                if not (row.get(required) or "").strip():
                    raise ConnectorParseError(
                        f"{csv_path.name}, row {row_number}: missing {required}"
                    )
            results.append(OpenInvoice(
                source_system=self.source_system,
                source_capability=SOURCE_CAPABILITY,
                invoice_id=row["invoice_id"].strip(),
                customer=row["customer"].strip(),
                invoice_date=_parse_date(row["invoice_date"], csv_path, row_number),
                due_date=_parse_date(row["due_date"], csv_path, row_number),
                amount=_parse_decimal(row.get("amount", ""), "amount", csv_path, row_number),
                currency=(row.get("currency") or "").strip() or "USD",
                last_payment_date=self._optional_date(row.get("last_payment_date"), csv_path, row_number),
                raw=dict(row),
            ))
        return results

    @staticmethod
    def _optional_date(raw_value: Optional[str], csv_path: Path, row_number: int) -> Optional[date]:
        if not (raw_value or "").strip():
            return None
        return _parse_date(raw_value, csv_path, row_number)
