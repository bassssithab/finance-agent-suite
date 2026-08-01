"""Local-folder stand-in for a real bank-feed/ERP connector.

Reads bank-statement and ledger-export CSVs from local folders and
normalizes both into Transaction records, until Phase 1 builds a connector
against a live bank feed or ERP API.

Expected CSV schemas:
    bank:   date,account,description,amount,balance,reference
            amount is already signed (deposit positive, withdrawal negative)
    ledger: date,account,memo,debit,credit,reference
            normalized amount = debit - credit (positive = net debit)
"""

import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional, Union

from .base import Connector, ConnectorParseError
from .models import Transaction


def _read_rows(csv_path: Path):
    with open(csv_path, newline="", encoding="utf-8") as f:
        yield from csv.DictReader(f)


def _parse_date(raw_value: str, csv_path: Path, row_number: int) -> date:
    try:
        return date.fromisoformat(raw_value.strip())
    except (ValueError, AttributeError) as exc:
        raise ConnectorParseError(
            f"{csv_path.name}, row {row_number}: invalid date {raw_value!r}"
        ) from exc


def _parse_decimal(raw_value: str, field_name: str, csv_path: Path, row_number: int) -> Decimal:
    try:
        return Decimal(raw_value.strip() or "0")
    except InvalidOperation as exc:
        raise ConnectorParseError(
            f"{csv_path.name}, row {row_number}: invalid {field_name} {raw_value!r}"
        ) from exc


class FileConnector(Connector):
    def __init__(
        self,
        source_system: str,
        bank_folder: Optional[Union[str, Path]] = None,
        ledger_folder: Optional[Union[str, Path]] = None,
    ):
        self.source_system = source_system
        self.bank_folder = Path(bank_folder) if bank_folder is not None else None
        self.ledger_folder = Path(ledger_folder) if ledger_folder is not None else None

    def fetch_transactions(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> list[Transaction]:
        transactions: list[Transaction] = []

        if self.bank_folder is not None and self.bank_folder.is_dir():
            for csv_path in sorted(self.bank_folder.glob("*.csv")):
                transactions.extend(self._parse_bank_file(csv_path))

        if self.ledger_folder is not None and self.ledger_folder.is_dir():
            for csv_path in sorted(self.ledger_folder.glob("*.csv")):
                transactions.extend(self._parse_ledger_file(csv_path))

        transactions = [
            t for t in transactions
            if (start_date is None or t.date >= start_date)
            and (end_date is None or t.date <= end_date)
        ]
        transactions.sort(key=lambda t: (t.date, t.account, t.reference or ""))
        return transactions

    def _parse_bank_file(self, csv_path: Path) -> list[Transaction]:
        results = []
        for row_number, row in enumerate(_read_rows(csv_path), start=2):
            results.append(Transaction(
                source_system=self.source_system,
                source_capability="bank",
                account=row["account"],
                date=_parse_date(row["date"], csv_path, row_number),
                amount=_parse_decimal(row["amount"], "amount", csv_path, row_number),
                currency=row.get("currency") or "USD",
                description=row["description"],
                reference=row.get("reference") or None,
                raw=dict(row),
            ))
        return results

    def _parse_ledger_file(self, csv_path: Path) -> list[Transaction]:
        results = []
        for row_number, row in enumerate(_read_rows(csv_path), start=2):
            debit = _parse_decimal(row.get("debit", ""), "debit", csv_path, row_number)
            credit = _parse_decimal(row.get("credit", ""), "credit", csv_path, row_number)
            results.append(Transaction(
                source_system=self.source_system,
                source_capability="erp",
                account=row["account"],
                date=_parse_date(row["date"], csv_path, row_number),
                amount=debit - credit,
                currency=row.get("currency") or "USD",
                description=row["memo"],
                reference=row.get("reference") or None,
                raw=dict(row),
            ))
        return results
