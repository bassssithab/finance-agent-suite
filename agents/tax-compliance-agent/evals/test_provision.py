import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from connectors import FileVatTransactionConnector, VatTransaction

from tax_compliance_agent import ProvisionPolicy, compute_provision

TXN_DIR = Path(__file__).parent / "fixtures" / "transactions"
DEFAULT_POLICY = ProvisionPolicy()


def txn(
    transaction_id="TXN-1",
    transaction_type="sale",
    amount="1000.00",
    vat_treatment="standard-rated",
    vat_rate="0.15",
    d="2026-07-02",
):
    return VatTransaction(
        source_system="test_co",
        source_capability="vat_transactions",
        transaction_id=transaction_id,
        date=date.fromisoformat(d),
        transaction_type=transaction_type,
        amount=Decimal(amount),
        vat_treatment=vat_treatment,
        vat_rate=Decimal(vat_rate) if vat_rate is not None else None,
        currency="USD",
        raw={},
    )


def codes(result):
    return sorted(a.code for a in result.anomalies)


def load(name):
    """Read one committed fixture CSV in isolation (the connector reads every
    CSV in a folder, so each scenario is copied into its own tmp dir)."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / f"{name}.csv").write_text((TXN_DIR / f"{name}.csv").read_text())
        return FileVatTransactionConnector(
            source_system="larenthia", folder=folder
        ).fetch_transactions()


# --- output / input VAT ---------------------------------------------------


def test_output_vat_on_a_standard_rated_sale():
    result = compute_provision([txn(amount="1000.00", vat_rate="0.15")], policy=DEFAULT_POLICY)
    assert result.output_vat_total == Decimal("150.00")
    assert result.input_vat_total == Decimal("0.00")
    ct = result.computed_transactions[0]
    assert ct.vat_direction == "output"
    assert ct.computed_vat == Decimal("150.00")


def test_non_standard_rated_sales_carry_no_vat():
    for treatment in ("zero-rated", "exempt", "out-of-scope"):
        result = compute_provision(
            [txn(vat_treatment=treatment, vat_rate=None)], policy=DEFAULT_POLICY
        )
        assert result.output_vat_total == Decimal("0.00"), treatment


def test_input_vat_on_a_standard_rated_purchase():
    result = compute_provision(
        [txn(transaction_type="purchase", amount="2000.00", vat_rate="0.15")],
        policy=DEFAULT_POLICY,
    )
    assert result.input_vat_total == Decimal("300.00")
    assert result.output_vat_total == Decimal("0.00")


def test_net_positions_payable_refundable_nil():
    payable = compute_provision(
        [txn("S", "sale", "1000.00"), txn("P", "purchase", "400.00")], policy=DEFAULT_POLICY
    )
    assert payable.net_vat == Decimal("90.00") and payable.position == "payable"

    refundable = compute_provision(
        [txn("S", "sale", "400.00"), txn("P", "purchase", "1000.00")], policy=DEFAULT_POLICY
    )
    assert refundable.net_vat == Decimal("-90.00") and refundable.position == "refundable"

    nil = compute_provision(
        [txn("S", "sale", "1000.00"), txn("P", "purchase", "1000.00")], policy=DEFAULT_POLICY
    )
    assert nil.net_vat == Decimal("0.00") and nil.position == "nil"


def test_by_treatment_breakdown_totals():
    result = compute_provision(
        [
            txn("S1", "sale", "1000.00", "standard-rated", "0.15"),
            txn("S2", "sale", "500.00", "zero-rated", None),
            txn("P1", "purchase", "200.00", "standard-rated", "0.15"),
        ],
        policy=DEFAULT_POLICY,
    )
    std = result.by_treatment["standard-rated"]
    assert std["sale"] == {"count": 1, "amount": Decimal("1000.00"), "vat": Decimal("150.00")}
    assert std["purchase"] == {"count": 1, "amount": Decimal("200.00"), "vat": Decimal("30.00")}
    assert result.by_treatment["zero-rated"]["sale"]["count"] == 1


# --- anomalies ---------------------------------------------------------


def test_net_refundable_position_anomaly_and_suppression():
    txns = [txn("S", "sale", "400.00"), txn("P", "purchase", "1000.00")]
    assert "net_refundable_position" in codes(compute_provision(txns, policy=DEFAULT_POLICY))

    quiet = ProvisionPolicy(flag_refundable=False)
    assert "net_refundable_position" not in codes(compute_provision(txns, policy=quiet))


def test_unrecognized_treatment_is_flagged_and_excluded_from_totals():
    for bad in ("reduced-rated", "", "   "):
        result = compute_provision([txn(vat_treatment=bad, vat_rate="0.05")], policy=DEFAULT_POLICY)
        assert codes(result) == ["unrecognized_treatment"]
        assert result.output_vat_total == Decimal("0.00")
        assert result.computed_transactions[0].included_in_totals is False


def test_treatment_rate_mismatch_both_directions():
    no_rate = compute_provision([txn(vat_treatment="standard-rated", vat_rate=None)], policy=DEFAULT_POLICY)
    assert codes(no_rate) == ["treatment_rate_mismatch"]
    assert "no VAT rate recorded" in no_rate.anomalies[0].detail

    zero_rate = compute_provision([txn(vat_treatment="standard-rated", vat_rate="0")], policy=DEFAULT_POLICY)
    assert codes(zero_rate) == ["treatment_rate_mismatch"]

    export_with_rate = compute_provision(
        [txn(vat_treatment="zero-rated", vat_rate="0.15")], policy=DEFAULT_POLICY
    )
    assert codes(export_with_rate) == ["treatment_rate_mismatch"]
    assert "nonzero VAT rate" in export_with_rate.anomalies[0].detail

    exempt_with_rate = compute_provision(
        [txn(vat_treatment="exempt", vat_rate="0.15")], policy=DEFAULT_POLICY
    )
    assert codes(exempt_with_rate) == ["treatment_rate_mismatch"]


def test_unrecognized_transaction_type_is_flagged_and_excluded():
    result = compute_provision([txn(transaction_type="refund")], policy=DEFAULT_POLICY)
    assert codes(result) == ["unrecognized_transaction_type"]
    assert result.output_vat_total == Decimal("0.00")
    assert result.computed_transactions[0].included_in_totals is False


def test_unexpected_standard_rate_only_when_policy_sets_it():
    off = compute_provision([txn(vat_rate="0.20")], policy=DEFAULT_POLICY)
    assert "unexpected_standard_rate" not in codes(off)

    on = compute_provision(
        [txn(vat_rate="0.20")], policy=ProvisionPolicy(expected_standard_rate=Decimal("0.15"))
    )
    assert "unexpected_standard_rate" in codes(on)


# --- canonicalisation ---------------------------------------------------


def test_zero_rated_export_alias_canonicalizes():
    result = compute_provision([txn(vat_treatment="Zero-Rated Export", vat_rate=None)], policy=DEFAULT_POLICY)
    assert result.computed_transactions[0].vat_treatment == "zero-rated"
    assert result.anomalies == []


def test_treatment_and_type_matching_is_case_insensitive():
    result = compute_provision(
        [txn(transaction_type="  SALE ", vat_treatment="Standard-Rated", vat_rate="0.15")],
        policy=DEFAULT_POLICY,
    )
    assert result.computed_transactions[0].transaction_type == "sale"
    assert result.output_vat_total == Decimal("150.00")
    assert result.anomalies == []


def test_vat_amounts_quantized_to_cents():
    result = compute_provision([txn(amount="333.33", vat_rate="0.15")], policy=DEFAULT_POLICY)
    # 333.33 * 0.15 = 49.9995 -> 50.00
    assert result.output_vat_total == Decimal("50.00")


# --- committed fixtures ----------------------------------------------


def test_normal_payable_fixture():
    result = compute_provision(load("normal_payable"), policy=DEFAULT_POLICY)
    assert result.output_vat_total == Decimal("24750.00")
    assert result.input_vat_total == Decimal("10200.00")
    assert result.net_vat == Decimal("14550.00")
    assert result.position == "payable"
    assert result.anomalies == []


def test_refundable_period_fixture():
    result = compute_provision(load("refundable_period"), policy=DEFAULT_POLICY)
    assert result.output_vat_total == Decimal("1800.00")
    assert result.input_vat_total == Decimal("16500.00")
    assert result.net_vat == Decimal("-14700.00")
    assert result.position == "refundable"
    assert codes(result) == ["net_refundable_position"]


def test_data_quality_issues_fixture():
    result = compute_provision(load("data_quality_issues"), policy=DEFAULT_POLICY)
    assert codes(result) == [
        "treatment_rate_mismatch",
        "treatment_rate_mismatch",
        "unrecognized_transaction_type",
        "unrecognized_treatment",
    ]
    assert result.excluded_transaction_ids == ["TXN-5003", "TXN-5004"]
    assert result.output_vat_total == Decimal("9000.00")
    assert result.input_vat_total == Decimal("3000.00")
    assert result.position == "payable"
