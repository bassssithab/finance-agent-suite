"""Local-folder stand-in for a real VAT transaction / tax-report export
connector.

Reads a folder of VAT transaction CSVs and normalizes each row into a
`VatTransaction` record, until Phase 1 builds a connector against a live ERP tax
report. Same read-first contract as the other connectors: there are no write
methods here — an agent never files, posts, or amends a VAT return, it only
reads the period's transactions and drafts a provision a human approves
(CLAUDE.md golden rules #1 and #2).

Expected CSV schema:
    transaction_id,date,transaction_type,amount,vat_treatment,vat_rate,currency

`vat_treatment`, `vat_rate` and `currency` are optional — a blank or missing
`vat_treatment` becomes "" and a blank `vat_rate` becomes `None` (the provision
calculation, not this connector, decides whether an unrecognised treatment or a
missing rate is an anomaly); a blank or missing `currency` becomes "USD".
`transaction_id`, `date`, `transaction_type` and `amount` are required — a blank
one raises `ConnectorParseError`.
"""

from abc import ABC, abstractmethod
from decimal import Decimal
from pathlib import Path
from typing import Optional, Union

from .base import ConnectorParseError
from .file_connector import _parse_date, _parse_decimal, _read_rows
from .models import VatTransaction

SOURCE_CAPABILITY = "vat_transactions"


class VatTransactionConnector(ABC):
    """Read-only interface for fetching a period's VAT transactions.

    Separate from `Connector` (which returns `Transaction` records built from
    debit/credit legs), `BudgetActualConnector`, `DocumentConnector` and
    `JournalEntryConnector`: this one deals in VAT-classified sale/purchase
    lines with their treatment and rate metadata.
    """

    @abstractmethod
    def fetch_transactions(self) -> list[VatTransaction]:
        """Return every VAT transaction, normalized and deterministically sorted."""
        raise NotImplementedError


class FileVatTransactionConnector(VatTransactionConnector):
    def __init__(self, source_system: str, folder: Optional[Union[str, Path]] = None):
        self.source_system = source_system
        self.folder = Path(folder) if folder is not None else None

    def fetch_transactions(self) -> list[VatTransaction]:
        transactions: list[VatTransaction] = []

        if self.folder is not None and self.folder.is_dir():
            for csv_path in sorted(self.folder.glob("*.csv")):
                transactions.extend(self._parse_file(csv_path))

        transactions.sort(key=lambda t: (t.date, t.transaction_id))
        return transactions

    def _parse_file(self, csv_path: Path) -> list[VatTransaction]:
        results: list[VatTransaction] = []
        for row_number, row in enumerate(_read_rows(csv_path), start=2):
            for required in ("transaction_id", "date", "transaction_type", "amount"):
                if not (row.get(required) or "").strip():
                    raise ConnectorParseError(
                        f"{csv_path.name}, row {row_number}: missing {required}"
                    )
            results.append(VatTransaction(
                source_system=self.source_system,
                source_capability=SOURCE_CAPABILITY,
                transaction_id=row["transaction_id"].strip(),
                date=_parse_date(row["date"], csv_path, row_number),
                transaction_type=row["transaction_type"].strip(),
                amount=_parse_decimal(row.get("amount", ""), "amount", csv_path, row_number),
                vat_treatment=(row.get("vat_treatment") or "").strip(),
                vat_rate=self._optional_rate(row.get("vat_rate"), csv_path, row_number),
                currency=(row.get("currency") or "").strip() or "USD",
                raw=dict(row),
            ))
        return results

    @staticmethod
    def _optional_rate(raw_value: Optional[str], csv_path: Path, row_number: int) -> Optional[Decimal]:
        if not (raw_value or "").strip():
            return None
        return _parse_decimal(raw_value, "vat_rate", csv_path, row_number)
