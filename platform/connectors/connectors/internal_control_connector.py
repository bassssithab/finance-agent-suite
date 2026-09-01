"""Local-folder stand-in for a real internal-controls-register connector (a GRC
tool or a controls spreadsheet export).

Reads a folder of controls CSVs and normalizes each row into an
`InternalControl` record, until Phase 1 builds a connector against a live GRC
API. Same read-first contract as the other connectors: there are no write
methods here — an agent never edits, adds, or retires a control, it only reads
the register and drafts something a human approves (CLAUDE.md golden rules #1
and #2).

Expected CSV schema:
    control_id,description,category

`category` is optional — a blank or missing `category` becomes "". `control_id`
and `description` are required — a blank one raises `ConnectorParseError`.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Union

from .base import ConnectorParseError
from .file_connector import _read_rows
from .models import InternalControl

SOURCE_CAPABILITY = "internal_controls"


class InternalControlConnector(ABC):
    """Read-only interface for fetching the company's internal-controls register.

    Separate from `Connector`, `BudgetActualConnector`, `DocumentConnector`,
    `JournalEntryConnector`, `OpenInvoiceConnector` and `VatTransactionConnector`:
    this one deals in internal-control descriptions with their category grouping.
    """

    @abstractmethod
    def fetch_controls(self) -> list[InternalControl]:
        """Return every control, normalized and deterministically sorted by id."""
        raise NotImplementedError


class FileInternalControlConnector(InternalControlConnector):
    def __init__(self, source_system: str, folder: Optional[Union[str, Path]] = None):
        self.source_system = source_system
        self.folder = Path(folder) if folder is not None else None

    def fetch_controls(self) -> list[InternalControl]:
        controls: list[InternalControl] = []

        if self.folder is not None and self.folder.is_dir():
            for csv_path in sorted(self.folder.glob("*.csv")):
                controls.extend(self._parse_file(csv_path))

        controls.sort(key=lambda c: c.control_id)
        return controls

    def _parse_file(self, csv_path: Path) -> list[InternalControl]:
        results: list[InternalControl] = []
        for row_number, row in enumerate(_read_rows(csv_path), start=2):
            for required in ("control_id", "description"):
                if not (row.get(required) or "").strip():
                    raise ConnectorParseError(
                        f"{csv_path.name}, row {row_number}: missing {required}"
                    )
            results.append(InternalControl(
                source_system=self.source_system,
                source_capability=SOURCE_CAPABILITY,
                control_id=row["control_id"].strip(),
                description=row["description"].strip(),
                category=(row.get("category") or "").strip(),
                raw=dict(row),
            ))
        return results
