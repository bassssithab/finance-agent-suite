"""The connector interface. See docs/ARCHITECTURE.md.

Read-first: every connector implements read access only. Write access
(posting a journal entry, sending an email) never happens here — it goes
through platform/approvals. Agents must call through a Connector rather than
talking to an external system directly (CLAUDE.md golden rule #1).
"""

from abc import ABC, abstractmethod
from datetime import date
from typing import Optional

from .models import Transaction


class Connector(ABC):
    @abstractmethod
    def fetch_transactions(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> list[Transaction]:
        """Return normalized transactions, optionally filtered to [start_date, end_date]."""
        raise NotImplementedError


class ConnectorParseError(Exception):
    """Raised when a source record can't be normalized into a Transaction."""
