"""Normalized record shape all connectors return. See docs/ARCHITECTURE.md."""

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
