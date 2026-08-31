"""Normalized record shapes all connectors return. See docs/ARCHITECTURE.md."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class Transaction:
    """One normalized transaction, regardless of which system it came from.

    `raw` keeps the original source row so a human or agent can always trace
    a normalized record back to the exact bank/ledger line it came from.
    """

    source_system: str
    source_capability: str
    account: str
    date: date
    amount: Decimal
    currency: str
    description: str
    reference: Optional[str]
    raw: dict[str, str]


@dataclass(frozen=True)
class BudgetActualLine:
    """One normalized planning/actuals line for a single period.

    A budget export and an actuals export share this shape; `source_capability`
    ("budget" or "actuals") is the only thing that distinguishes them, the same
    way `Transaction.source_capability` distinguishes a bank line from a ledger
    line. `raw` keeps the original source row so a human or agent can trace a
    normalized record back to the exact CSV line it came from.
    """

    source_system: str
    source_capability: str  # "budget" | "actuals"
    period: str             # as given, e.g. "2026-07" — no normalization
    account: str            # GL account code, e.g. "6000"
    line_item: str          # human label, e.g. "Marketing — paid media"
    category: str            # optional grouping, e.g. "Operating expenses"; "" if absent
    amount: Decimal
    currency: str
    raw: dict[str, str]


@dataclass(frozen=True)
class SourceDocument:
    """One binary source document (a scanned invoice, receipt, contract PDF)
    handed to an agent by a document connector.

    Read-first, same contract as `Transaction`: the connector returns the raw
    bytes plus enough identity to trace and cite the document, and nothing
    else. `sha256` is over `content` — an agent records it in its own
    `AuditEvent` so the exact file a run acted on can always be identified,
    without copying the bytes into the audit log.
    """

    source_system: str
    source_capability: str  # always "documents"
    document_id: str        # stable id within the connector (here: the filename)
    filename: str
    media_type: str         # e.g. "image/png", "image/jpeg", "application/pdf"
    content: bytes
    size_bytes: int
    sha256: str
