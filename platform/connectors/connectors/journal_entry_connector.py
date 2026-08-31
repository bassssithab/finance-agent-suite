"""Local-folder stand-in for a real journal-entry / GL export connector.

Reads a folder of journal-entry CSVs and normalizes each row into a
`JournalEntry` record, until Phase 1 builds a connector against a live ERP GL
API. Same read-first contract as the other connectors: there are no write
methods here — an agent never posts, blocks, or edits an entry, it only reads
the entries and drafts something a human approves (CLAUDE.md golden rules #1
and #2).

Expected CSV schema:
    entry_id,date,account,amount,preparer,approver_1,approver_2,currency

`approver_2` and `currency` are optional — a blank or missing `approver_1` /
`approver_2` becomes `None` (the control test decides whether an unapproved or
singly-approved entry is a violation, not this connector); a blank or missing
`currency` becomes "USD". `entry_id`, `date`, `account`, `amount` and
`preparer` are required — a blank one raises `ConnectorParseError`.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Union

from .base import ConnectorParseError
from .file_connector import _parse_date, _parse_decimal, _read_rows
from .models import JournalEntry

SOURCE_CAPABILITY = "journal_entries"


class JournalEntryConnector(ABC):
    """Read-only interface for fetching journal-entry headers.

    Separate from `Connector` (which returns `Transaction` records built from
    debit/credit legs), `BudgetActualConnector` and `DocumentConnector`: this
    one deals in journal-entry headers with their preparer/approver metadata.
    """

    @abstractmethod
    def fetch_entries(self) -> list[JournalEntry]:
        """Return every journal entry, normalized and deterministically sorted."""
        raise NotImplementedError


class FileJournalEntryConnector(JournalEntryConnector):
    def __init__(self, source_system: str, folder: Optional[Union[str, Path]] = None):
        self.source_system = source_system
        self.folder = Path(folder) if folder is not None else None

    def fetch_entries(self) -> list[JournalEntry]:
        entries: list[JournalEntry] = []

        if self.folder is not None and self.folder.is_dir():
            for csv_path in sorted(self.folder.glob("*.csv")):
                entries.extend(self._parse_file(csv_path))

        entries.sort(key=lambda e: (e.date, e.entry_id))
        return entries

    def _parse_file(self, csv_path: Path) -> list[JournalEntry]:
        results: list[JournalEntry] = []
        for row_number, row in enumerate(_read_rows(csv_path), start=2):
            for required in ("entry_id", "date", "account", "amount", "preparer"):
                if not (row.get(required) or "").strip():
                    raise ConnectorParseError(
                        f"{csv_path.name}, row {row_number}: missing {required}"
                    )
            results.append(JournalEntry(
                source_system=self.source_system,
                source_capability=SOURCE_CAPABILITY,
                entry_id=row["entry_id"].strip(),
                date=_parse_date(row["date"], csv_path, row_number),
                account=row["account"].strip(),
                amount=_parse_decimal(row.get("amount", ""), "amount", csv_path, row_number),
                currency=(row.get("currency") or "").strip() or "USD",
                preparer=row["preparer"].strip(),
                approver_1=(row.get("approver_1") or "").strip() or None,
                approver_2=(row.get("approver_2") or "").strip() or None,
                raw=dict(row),
            ))
        return results
