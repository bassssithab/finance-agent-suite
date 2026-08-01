import json
from datetime import date
from decimal import Decimal

from connectors import Transaction
from reconciliation_agent import MatchedPair, ReconciliationReport, UnmatchedTransaction


def make_txn(capability, amount, d=date(2026, 7, 1), reference="REF001"):
    return Transaction(
        source_system="sample_co",
        source_capability=capability,
        account="acct",
        date=d,
        amount=Decimal(amount),
        currency="USD",
        description="txn",
        reference=reference,
        raw={"amount": amount},
    )


def test_report_totals_and_difference_include_matched_and_exceptions():
    bank_matched = make_txn("bank", "1000.00")
    ledger_matched = make_txn("erp", "1000.00")
    bank_only = make_txn("bank", "75.00", reference="REF999")
    ledger_only = make_txn("erp", "15.00", reference="LREF101")

    report = ReconciliationReport(
        source_system="sample_co",
        generated_at="2026-08-01T00:00:00Z",
        matched=[MatchedPair(bank=bank_matched, ledger=ledger_matched, match_type="exact", date_delta_days=0)],
        exceptions=[
            UnmatchedTransaction(side="bank", transaction=bank_only, reason="no match"),
            UnmatchedTransaction(side="ledger", transaction=ledger_only, reason="no match"),
        ],
    )

    assert report.bank_total == Decimal("1075.00")
    assert report.ledger_total == Decimal("1015.00")
    assert report.difference == Decimal("60.00")


def test_to_dict_is_json_safe_and_summarizes_counts():
    bank_matched = make_txn("bank", "1000.00")
    ledger_matched = make_txn("erp", "1000.00")
    bank_only = make_txn("bank", "75.00", reference="REF999")

    report = ReconciliationReport(
        source_system="sample_co",
        generated_at="2026-08-01T00:00:00Z",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 31),
        matched=[MatchedPair(bank=bank_matched, ledger=ledger_matched, match_type="exact", date_delta_days=0)],
        exceptions=[UnmatchedTransaction(side="bank", transaction=bank_only, reason="no match")],
    )

    payload = report.to_dict()

    assert payload["start_date"] == "2026-07-01"
    assert payload["end_date"] == "2026-07-31"
    assert payload["summary"]["matched_exact_count"] == 1
    assert payload["summary"]["matched_tolerance_count"] == 0
    assert payload["summary"]["bank_exception_count"] == 1
    assert payload["summary"]["ledger_exception_count"] == 0
    assert payload["summary"]["bank_total"] == "1075.00"
    assert payload["matched"][0]["bank"]["amount"] == "1000.00"
    assert payload["exceptions"][0]["reason"] == "no match"

    json.dumps(payload)  # must not raise: everything is JSON-serializable
