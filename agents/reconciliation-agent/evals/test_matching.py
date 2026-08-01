from datetime import date
from decimal import Decimal

from connectors import Transaction
from reconciliation_agent import match_transactions


def make_txn(capability, account="chase-1234", d=date(2026, 7, 1), amount="100.00",
             reference="REF001", description="txn"):
    return Transaction(
        source_system="sample_co",
        source_capability=capability,
        account=account,
        date=d,
        amount=Decimal(amount),
        currency="USD",
        description=description,
        reference=reference,
        raw={},
    )


def bank(**kwargs):
    return make_txn("bank", **kwargs)


def ledger(**kwargs):
    return make_txn("erp", **kwargs)


def test_exact_match_on_amount_date_reference():
    b = bank(amount="1000.00", d=date(2026, 7, 1), reference="REF001")
    l = ledger(amount="1000.00", d=date(2026, 7, 1), reference="REF001")

    result = match_transactions([b], [l])

    assert len(result.matched) == 1
    assert result.matched[0].match_type == "exact"
    assert result.matched[0].date_delta_days == 0
    assert not result.exceptions


def test_exact_match_requires_reference_on_both_sides():
    b = bank(amount="500.00", d=date(2026, 7, 1), reference=None)
    l = ledger(amount="500.00", d=date(2026, 7, 1), reference="LREF100")

    result = match_transactions([b], [l], tolerance_days=0)

    # No reference on the bank side means the exact pass can't match it, and
    # tolerance_days=0 still allows an amount+date match.
    assert len(result.matched) == 1
    assert result.matched[0].match_type == "tolerance"


def test_tolerance_match_within_window():
    b = bank(amount="500.00", d=date(2026, 7, 20), reference=None)
    l = ledger(amount="500.00", d=date(2026, 7, 22), reference="LREF100")

    result = match_transactions([b], [l], tolerance_days=2)

    assert len(result.matched) == 1
    assert result.matched[0].match_type == "tolerance"
    assert result.matched[0].date_delta_days == 2
    assert not result.exceptions


def test_no_match_outside_tolerance_window():
    b = bank(amount="500.00", d=date(2026, 7, 20), reference=None)
    l = ledger(amount="500.00", d=date(2026, 7, 23), reference="LREF100")

    result = match_transactions([b], [l], tolerance_days=2)

    assert not result.matched
    assert len(result.exceptions) == 2
    sides = {e.side for e in result.exceptions}
    assert sides == {"bank", "ledger"}


def test_unmatched_bank_and_ledger_become_exceptions_with_reasons():
    b = bank(amount="75.00", d=date(2026, 7, 25), reference="REF999")
    l = ledger(amount="15.00", d=date(2026, 7, 10), reference="LREF101")

    result = match_transactions([b], [l])

    assert not result.matched
    assert len(result.exceptions) == 2
    bank_exc = next(e for e in result.exceptions if e.side == "bank")
    ledger_exc = next(e for e in result.exceptions if e.side == "ledger")
    assert "ledger" in bank_exc.reason
    assert "bank" in ledger_exc.reason


def test_duplicate_amount_date_reference_pairs_one_to_one():
    bank_txns = [
        bank(amount="100.00", d=date(2026, 7, 1), reference="DUPREF"),
        bank(amount="100.00", d=date(2026, 7, 1), reference="DUPREF"),
    ]
    ledger_txns = [
        ledger(amount="100.00", d=date(2026, 7, 1), reference="DUPREF"),
    ]

    result = match_transactions(bank_txns, ledger_txns)

    # Only one ledger candidate exists, so only one bank txn can match exactly;
    # the second falls through and becomes an exception (no double-counting).
    assert len(result.matched) == 1
    assert len(result.exceptions) == 1
    assert result.exceptions[0].side == "bank"


def test_tolerance_pass_prefers_closest_date_when_multiple_candidates():
    b = bank(amount="200.00", d=date(2026, 7, 10), reference=None)
    ledger_txns = [
        ledger(amount="200.00", d=date(2026, 7, 12), reference="FAR"),
        ledger(amount="200.00", d=date(2026, 7, 11), reference="NEAR"),
    ]

    result = match_transactions([b], ledger_txns, tolerance_days=2)

    assert len(result.matched) == 1
    assert result.matched[0].ledger.reference == "NEAR"
    assert result.matched[0].date_delta_days == 1
    assert len(result.exceptions) == 1
    assert result.exceptions[0].transaction.reference == "FAR"
