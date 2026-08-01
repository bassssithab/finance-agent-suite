"""Deterministic bank <-> ledger transaction matching.

Two passes, in order:
1. Exact: amount + date + reference (only where both sides have a reference).
2. Tolerance: amount exact, date within `tolerance_days`.

Everything still unmatched after both passes becomes an exception. No LLM
involvement — this is plain code so results are reproducible and testable.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from connectors import Transaction

from .models import MatchedPair, UnmatchedTransaction

DEFAULT_TOLERANCE_DAYS = 2


@dataclass
class MatchResult:
    matched: list[MatchedPair] = field(default_factory=list)
    exceptions: list[UnmatchedTransaction] = field(default_factory=list)


def match_transactions(
    bank: list[Transaction],
    ledger: list[Transaction],
    tolerance_days: int = DEFAULT_TOLERANCE_DAYS,
) -> MatchResult:
    matched: list[MatchedPair] = []

    exact_matched, bank, ledger = _match_exact(bank, ledger)
    matched.extend(exact_matched)

    tolerance_matched, bank, ledger = _match_tolerance(bank, ledger, tolerance_days)
    matched.extend(tolerance_matched)

    exceptions = [
        UnmatchedTransaction(
            side="bank", transaction=t,
            reason="no matching ledger transaction found within tolerance",
        )
        for t in bank
    ] + [
        UnmatchedTransaction(
            side="ledger", transaction=t,
            reason="no matching bank transaction found within tolerance",
        )
        for t in ledger
    ]
    return MatchResult(matched=matched, exceptions=exceptions)


def _match_exact(
    bank: list[Transaction], ledger: list[Transaction],
) -> tuple[list[MatchedPair], list[Transaction], list[Transaction]]:
    ledger_by_key: dict[tuple, list[int]] = defaultdict(list)
    for idx, t in enumerate(ledger):
        if t.reference:
            ledger_by_key[(t.amount, t.date, t.reference)].append(idx)

    matched: list[MatchedPair] = []
    consumed: set[int] = set()
    remaining_bank: list[Transaction] = []

    for t in bank:
        candidate_idx = None
        if t.reference:
            for idx in ledger_by_key.get((t.amount, t.date, t.reference), []):
                if idx not in consumed:
                    candidate_idx = idx
                    break
        if candidate_idx is None:
            remaining_bank.append(t)
            continue
        consumed.add(candidate_idx)
        matched.append(MatchedPair(
            bank=t, ledger=ledger[candidate_idx], match_type="exact", date_delta_days=0,
        ))

    remaining_ledger = [t for idx, t in enumerate(ledger) if idx not in consumed]
    return matched, remaining_bank, remaining_ledger


def _match_tolerance(
    bank: list[Transaction], ledger: list[Transaction], tolerance_days: int,
) -> tuple[list[MatchedPair], list[Transaction], list[Transaction]]:
    ledger_by_amount: dict[Decimal, list[int]] = defaultdict(list)
    for idx, t in enumerate(ledger):
        ledger_by_amount[t.amount].append(idx)

    matched: list[MatchedPair] = []
    consumed: set[int] = set()
    remaining_bank: list[Transaction] = []

    for t in bank:
        candidates = [
            idx for idx in ledger_by_amount.get(t.amount, [])
            if idx not in consumed and abs((t.date - ledger[idx].date).days) <= tolerance_days
        ]
        if not candidates:
            remaining_bank.append(t)
            continue
        # Deterministic tie-break: smallest date delta, then earliest ledger date, then input order.
        best_idx = min(
            candidates,
            key=lambda idx: (abs((t.date - ledger[idx].date).days), ledger[idx].date, idx),
        )
        consumed.add(best_idx)
        matched.append(MatchedPair(
            bank=t, ledger=ledger[best_idx], match_type="tolerance",
            date_delta_days=abs((t.date - ledger[best_idx].date).days),
        ))

    remaining_ledger = [t for idx, t in enumerate(ledger) if idx not in consumed]
    return matched, remaining_bank, remaining_ledger
