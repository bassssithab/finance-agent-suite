"""Local-folder stand-in for a real budget / actuals feed (planning system or
ERP export).

Reads a period's budget CSVs and actuals CSVs from local folders and
normalizes both into `BudgetActualLine` records, until Phase 1 builds a
connector against a live planning tool or ERP API. Same read-first contract as
`FileConnector`: there are no write methods here — anything an agent produces
from these lines goes through `platform/approvals` (CLAUDE.md golden rules #1
and #2).

Expected CSV schema (budget and actuals are identical in shape):
    period,account,line_item,category,amount,currency

`category` and `currency` are optional — a blank or missing `category` becomes
"", a blank or missing `currency` becomes "USD".
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Union

from .base import ConnectorParseError
from .file_connector import _parse_decimal, _read_rows
from .models import BudgetActualLine


class BudgetActualConnector(ABC):
    """Read-only interface for fetching a period's budget and actuals lines.

    Separate from `Connector` (which returns `Transaction` records) and
    `DocumentConnector` (which returns raw files): this one deals in normalized
    planning/actuals rows.
    """

    @abstractmethod
    def fetch_lines(self, period: Optional[str] = None) -> list[BudgetActualLine]:
        """Return normalized budget + actuals lines, optionally filtered to a
        single `period` (exact string match)."""
        raise NotImplementedError


class FileBudgetActualConnector(BudgetActualConnector):
    def __init__(
        self,
        source_system: str,
        budget_folder: Optional[Union[str, Path]] = None,
        actuals_folder: Optional[Union[str, Path]] = None,
    ):
        self.source_system = source_system
        self.budget_folder = Path(budget_folder) if budget_folder is not None else None
        self.actuals_folder = Path(actuals_folder) if actuals_folder is not None else None

    def fetch_lines(self, period: Optional[str] = None) -> list[BudgetActualLine]:
        lines: list[BudgetActualLine] = []

        if self.budget_folder is not None and self.budget_folder.is_dir():
            for csv_path in sorted(self.budget_folder.glob("*.csv")):
                lines.extend(self._parse_file(csv_path, "budget"))

        if self.actuals_folder is not None and self.actuals_folder.is_dir():
            for csv_path in sorted(self.actuals_folder.glob("*.csv")):
                lines.extend(self._parse_file(csv_path, "actuals"))

        if period is not None:
            lines = [line for line in lines if line.period == period]

        lines.sort(key=lambda line: (line.source_capability, line.account, line.line_item))
        return lines

    def _parse_file(self, csv_path: Path, capability: str) -> list[BudgetActualLine]:
        results: list[BudgetActualLine] = []
        for row_number, row in enumerate(_read_rows(csv_path), start=2):
            for required in ("period", "account", "line_item"):
                if not (row.get(required) or "").strip():
                    raise ConnectorParseError(
                        f"{csv_path.name}, row {row_number}: missing {required}"
                    )
            results.append(BudgetActualLine(
                source_system=self.source_system,
                source_capability=capability,
                period=row["period"].strip(),
                account=row["account"].strip(),
                line_item=row["line_item"].strip(),
                category=(row.get("category") or "").strip(),
                amount=_parse_decimal(row.get("amount", ""), "amount", csv_path, row_number),
                currency=(row.get("currency") or "").strip() or "USD",
                raw=dict(row),
            ))
        return results
