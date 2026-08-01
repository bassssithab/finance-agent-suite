"""Data model for reconciliation results.

Matching bank lines against ledger lines is record-linkage, not an
accounting treatment — no ASC/IFRS standard applies to this logic.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from connectors import Transaction


def serialize_transaction(t: Transaction) -> dict:
    """JSON-safe view of a Transaction (Decimal/date -> str) for audit events and reports."""
    return {
        "source_system": t.source_system,
        "source_capability": t.source_capability,
        "account": t.account,
        "date": t.date.isoformat(),
        "amount": str(t.amount),
        "currency": t.currency,
        "description": t.description,
        "reference": t.reference,
        "raw": t.raw,
    }


@dataclass(frozen=True)
class MatchedPair:
    bank: Transaction
    ledger: Transaction
    match_type: str  # "exact" | "tolerance"
    date_delta_days: int


@dataclass(frozen=True)
class UnmatchedTransaction:
    side: str  # "bank" | "ledger"
    transaction: Transaction
    reason: str


@dataclass
class ReconciliationReport:
    source_system: str
    generated_at: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    matched: list[MatchedPair] = field(default_factory=list)
    exceptions: list[UnmatchedTransaction] = field(default_factory=list)

    @property
    def bank_total(self) -> Decimal:
        total = sum((p.bank.amount for p in self.matched), Decimal("0"))
        total += sum(
            (e.transaction.amount for e in self.exceptions if e.side == "bank"), Decimal("0")
        )
        return total

    @property
    def ledger_total(self) -> Decimal:
        total = sum((p.ledger.amount for p in self.matched), Decimal("0"))
        total += sum(
            (e.transaction.amount for e in self.exceptions if e.side == "ledger"), Decimal("0")
        )
        return total

    @property
    def difference(self) -> Decimal:
        return self.bank_total - self.ledger_total

    def to_dict(self) -> dict:
        exact = [p for p in self.matched if p.match_type == "exact"]
        tolerance = [p for p in self.matched if p.match_type == "tolerance"]
        bank_exceptions = [e for e in self.exceptions if e.side == "bank"]
        ledger_exceptions = [e for e in self.exceptions if e.side == "ledger"]

        return {
            "source_system": self.source_system,
            "generated_at": self.generated_at,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "summary": {
                "matched_exact_count": len(exact),
                "matched_tolerance_count": len(tolerance),
                "bank_exception_count": len(bank_exceptions),
                "ledger_exception_count": len(ledger_exceptions),
                "bank_total": str(self.bank_total),
                "ledger_total": str(self.ledger_total),
                "difference": str(self.difference),
            },
            "matched": [
                {
                    "match_type": p.match_type,
                    "date_delta_days": p.date_delta_days,
                    "bank": serialize_transaction(p.bank),
                    "ledger": serialize_transaction(p.ledger),
                }
                for p in self.matched
            ],
            "exceptions": [
                {
                    "side": e.side,
                    "reason": e.reason,
                    "transaction": serialize_transaction(e.transaction),
                }
                for e in self.exceptions
            ],
        }
