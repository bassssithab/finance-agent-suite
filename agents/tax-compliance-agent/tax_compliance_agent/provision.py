"""Deterministic period-end VAT provision calculation and anomaly flagging.

No LLM involvement (CLAUDE.md rule #4): canonicalising the treatment/type
strings, computing output VAT (VAT on sales) and input VAT (VAT on purchases),
netting them into the period's payable-or-refundable position, and every anomaly
check run in plain `Decimal` code so the result for every transaction and for
the period is reproducible and testable. The model's only later job (in
`narrate.py`) is to write the filing-support narrative and flag which anomalies
need a specialist.

The four VAT treatment categories and the 15% standard rate are the same
fictional Larenthia VAT code that `agents/vat-treatment-agent` classifies
against (see `evals/fixtures.py`). This module encodes only the arithmetic of a
period return — output VAT less recoverable input VAT — not an accounting
treatment, so no ASC/IFRS reference is encoded (same stance as
`reconciliation_agent.matching`, `ap_agent.sanity` and `close_agent.variance`).
Partial exemption / input-VAT attribution and reverse charge are out of scope
for this task.
"""

from decimal import ROUND_HALF_UP, Decimal

from connectors import VatTransaction

from .models import (
    Anomaly,
    ComputedTransaction,
    ProvisionPolicy,
    VAT_TREATMENTS,
    VatProvisionResult,
)

_CENTS = Decimal("0.01")
_ZERO_RATE_TREATMENTS = ("zero-rated", "exempt", "out-of-scope")


def _canon_treatment(raw: str) -> str:
    s = (raw or "").strip().casefold()
    if s in ("zero-rated export", "zero-rated exports"):
        return "zero-rated"
    if s in VAT_TREATMENTS:
        return s
    return "unrecognized"


def _canon_type(raw: str) -> str:
    s = (raw or "").strip().casefold()
    if s in ("sale", "sales"):
        return "sale"
    if s in ("purchase", "purchases"):
        return "purchase"
    return "unrecognized"


def _fmt_money(value: Decimal) -> str:
    return f"{value:,.2f}"


def _fmt_rate(rate) -> str:
    return "no rate" if rate is None else str(rate)


def _empty_breakdown() -> dict:
    return {
        treatment: {
            side: {"count": 0, "amount": Decimal("0"), "vat": Decimal("0")}
            for side in ("sale", "purchase")
        }
        for treatment in VAT_TREATMENTS
    }


def compute_provision(
    transactions: list[VatTransaction],
    *,
    policy: ProvisionPolicy,
) -> VatProvisionResult:
    computed: list[ComputedTransaction] = []
    by_treatment = _empty_breakdown()
    output_vat_total = Decimal("0")
    input_vat_total = Decimal("0")
    anomalies: list[Anomaly] = []

    for txn in transactions:
        ttype = _canon_type(txn.transaction_type)
        treatment = _canon_treatment(txn.vat_treatment)
        recognized = ttype != "unrecognized" and treatment != "unrecognized"
        direction = {"sale": "output", "purchase": "input"}.get(ttype, "none")

        if recognized and treatment == "standard-rated":
            rate = txn.vat_rate if txn.vat_rate is not None else Decimal("0")
            computed_vat = (txn.amount * rate).quantize(_CENTS, rounding=ROUND_HALF_UP)
        else:
            computed_vat = Decimal("0")

        if recognized:
            if direction == "output":
                output_vat_total += computed_vat
            elif direction == "input":
                input_vat_total += computed_vat
            cell = by_treatment[treatment][ttype]
            cell["count"] += 1
            cell["amount"] += txn.amount
            cell["vat"] += computed_vat

        computed.append(ComputedTransaction(
            transaction_id=txn.transaction_id,
            date=txn.date.isoformat(),
            transaction_type=ttype,
            raw_transaction_type=txn.transaction_type,
            amount=txn.amount,
            vat_treatment=treatment,
            raw_vat_treatment=txn.vat_treatment,
            vat_rate=txn.vat_rate,
            vat_direction=direction,
            computed_vat=computed_vat,
            included_in_totals=recognized,
            currency=txn.currency,
        ))

        # --- per-transaction anomalies -----------------------------------
        if ttype == "unrecognized":
            anomalies.append(Anomaly(
                code="unrecognized_transaction_type",
                transaction_id=txn.transaction_id,
                detail=(
                    f"transaction {txn.transaction_id} has transaction_type "
                    f"{txn.transaction_type!r}, which is neither 'sale' nor 'purchase'; "
                    "it is excluded from the VAT totals and needs review"
                ),
            ))
        if treatment == "unrecognized":
            anomalies.append(Anomaly(
                code="unrecognized_treatment",
                transaction_id=txn.transaction_id,
                detail=(
                    f"transaction {txn.transaction_id} has vat_treatment "
                    f"{txn.vat_treatment!r}, which is not one of "
                    f"{', '.join(VAT_TREATMENTS)}; it is excluded from the VAT totals "
                    "and needs review"
                ),
            ))
        else:
            if treatment == "standard-rated" and (txn.vat_rate is None or txn.vat_rate == 0):
                anomalies.append(Anomaly(
                    code="treatment_rate_mismatch",
                    transaction_id=txn.transaction_id,
                    detail=(
                        f"transaction {txn.transaction_id} is standard-rated but has "
                        f"{'no VAT rate recorded' if txn.vat_rate is None else 'a zero VAT rate'}; "
                        f"it contributes {_fmt_money(computed_vat)} VAT until the rate is supplied"
                    ),
                ))
            elif (
                treatment in _ZERO_RATE_TREATMENTS
                and txn.vat_rate is not None
                and txn.vat_rate != 0
            ):
                anomalies.append(Anomaly(
                    code="treatment_rate_mismatch",
                    transaction_id=txn.transaction_id,
                    detail=(
                        f"transaction {txn.transaction_id} is {treatment} but carries a "
                        f"nonzero VAT rate {txn.vat_rate}; {treatment} supplies bear no VAT"
                    ),
                ))
            if (
                treatment == "standard-rated"
                and policy.expected_standard_rate is not None
                and txn.vat_rate is not None
                and txn.vat_rate != policy.expected_standard_rate
            ):
                anomalies.append(Anomaly(
                    code="unexpected_standard_rate",
                    transaction_id=txn.transaction_id,
                    detail=(
                        f"transaction {txn.transaction_id} is standard-rated at "
                        f"{txn.vat_rate}, which differs from the expected standard rate "
                        f"{policy.expected_standard_rate}"
                    ),
                ))

    net_vat = output_vat_total - input_vat_total
    if net_vat > 0:
        position = "payable"
    elif net_vat < 0:
        position = "refundable"
    else:
        position = "nil"

    if policy.flag_refundable and net_vat < 0:
        anomalies.append(Anomaly(
            code="net_refundable_position",
            transaction_id=None,
            detail=(
                f"the period nets to a refundable position: output VAT "
                f"{_fmt_money(output_vat_total)} less input VAT {_fmt_money(input_vat_total)} "
                f"= {_fmt_money(net_vat)}. A net refund is unusual for most trading "
                "businesses and warrants a second look before filing"
            ),
        ))

    anomalies.sort(key=lambda a: (a.code, a.transaction_id or ""))

    return VatProvisionResult(
        computed_transactions=computed,
        output_vat_total=output_vat_total.quantize(_CENTS, rounding=ROUND_HALF_UP),
        input_vat_total=input_vat_total.quantize(_CENTS, rounding=ROUND_HALF_UP),
        net_vat=net_vat.quantize(_CENTS, rounding=ROUND_HALF_UP),
        position=position,
        by_treatment=by_treatment,
        anomalies=anomalies,
    )
