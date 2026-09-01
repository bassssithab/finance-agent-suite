"""Data model for the tax-compliance-agent VAT provision workflow: the
provision policy, one computed transaction, one flagged anomaly, the drafted
filing-support narrative, and the report that goes to approvals.

A `VatProvisionReport` is never a filed return and never tax advice — CLAUDE.md
rule #2 requires it to go through `platform/approvals` before anything is acted
on, and the return itself is filed by a qualified person, not this agent. This
module only shapes the data and its audit/approval payloads; the deterministic
calculation lives in `provision.py` (plain code, rule #4), the narrative
drafting in `narrate.py` (the only LLM call), orchestration in `runner.py`.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

# The four VAT treatment categories, identical to agents/vat-treatment-agent's
# fictional Larenthia VAT code (see evals/fixtures.py). "zero-rated export" is
# accepted as an alias for "zero-rated".
VAT_TREATMENTS = ("standard-rated", "zero-rated", "exempt", "out-of-scope")
TRANSACTION_TYPES = ("sale", "purchase")

# Anomaly codes, produced by provision.py:
#   "net_refundable_position"       — the period nets to a refund (input > output VAT)
#   "unrecognized_treatment"        — vat_treatment is blank or not one of VAT_TREATMENTS
#   "treatment_rate_mismatch"       — standard-rated with no/zero rate, or a
#                                     non-standard-rated line carrying a nonzero rate
#   "unrecognized_transaction_type" — transaction_type is not sale/purchase
#   "unexpected_standard_rate"      — standard-rated rate != policy.expected_standard_rate
#                                     (only when that policy field is set)
ANOMALY_CODES = (
    "net_refundable_position",
    "unrecognized_treatment",
    "treatment_rate_mismatch",
    "unrecognized_transaction_type",
    "unexpected_standard_rate",
)


# ---------------------------------------------------------------------------
# Provision policy (consumed by provision.py) — configurable per run
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProvisionPolicy:
    """The company-policy inputs to the deterministic provision calculation.

    `flag_refundable` controls whether a net refundable position raises the
    `net_refundable_position` anomaly (on by default — it is unusual for most
    trading businesses and worth a second look). `expected_standard_rate`, when
    set, flags any standard-rated transaction whose recorded rate differs from
    it; left `None` by default so the base behaviour is exactly the three core
    anomaly checks plus the transaction-type safety net.
    """

    flag_refundable: bool = True
    expected_standard_rate: Optional[Decimal] = None

    def to_dict(self) -> dict:
        return {
            "flag_refundable": self.flag_refundable,
            "expected_standard_rate": (
                str(self.expected_standard_rate)
                if self.expected_standard_rate is not None
                else None
            ),
        }


# ---------------------------------------------------------------------------
# One computed transaction (produced by provision.py) — pure arithmetic, no LLM
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ComputedTransaction:
    transaction_id: str
    date: str                       # ISO date, as normalized by the connector
    transaction_type: str           # normalized: "sale" | "purchase" | "unrecognized"
    raw_transaction_type: str
    amount: Decimal
    vat_treatment: str              # canonicalized: one of VAT_TREATMENTS | "unrecognized"
    raw_vat_treatment: str
    vat_rate: Optional[Decimal]
    vat_direction: str              # "output" (sale) | "input" (purchase) | "none"
    computed_vat: Decimal           # amount * rate for standard-rated; 0 otherwise
    included_in_totals: bool        # False for an unrecognized treatment / type
    currency: str

    def to_dict(self) -> dict:
        return {
            "transaction_id": self.transaction_id,
            "date": self.date,
            "transaction_type": self.transaction_type,
            "raw_transaction_type": self.raw_transaction_type,
            "amount": str(self.amount),
            "vat_treatment": self.vat_treatment,
            "raw_vat_treatment": self.raw_vat_treatment,
            "vat_rate": str(self.vat_rate) if self.vat_rate is not None else None,
            "vat_direction": self.vat_direction,
            "computed_vat": str(self.computed_vat),
            "included_in_totals": self.included_in_totals,
            "currency": self.currency,
        }


# ---------------------------------------------------------------------------
# One flagged anomaly (produced by provision.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Anomaly:
    code: str
    transaction_id: Optional[str]   # None for a whole-period anomaly (refundable position)
    detail: str                     # deterministic human-readable reason, safe for the audit log

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "transaction_id": self.transaction_id,
            "detail": self.detail,
        }


# ---------------------------------------------------------------------------
# The deterministic provision result (provision.py output)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VatProvisionResult:
    computed_transactions: list        # list[ComputedTransaction]
    output_vat_total: Decimal
    input_vat_total: Decimal
    net_vat: Decimal                  # output_vat_total - input_vat_total
    position: str                     # "payable" | "refundable" | "nil"
    by_treatment: dict                # {treatment: {"sale": {...}, "purchase": {...}}}
    anomalies: list                   # list[Anomaly]

    @property
    def excluded_transaction_ids(self) -> list:
        return [ct.transaction_id for ct in self.computed_transactions if not ct.included_in_totals]


# ---------------------------------------------------------------------------
# One drafted filing-support narrative (produced by narrate.py from the tool call)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FilingSupportNarrative:
    position_summary: str
    anomaly_explanations: list        # one plain-English entry per anomaly
    specialist_review_needed: bool    # does any anomaly warrant a tax specialist before filing
    citations: list                   # citation labels relied on; [] when ungrounded

    def to_dict(self) -> dict:
        return {
            "position_summary": self.position_summary,
            "anomaly_explanations": list(self.anomaly_explanations),
            "specialist_review_needed": self.specialist_review_needed,
            "citations": list(self.citations),
        }


# ---------------------------------------------------------------------------
# The VAT provision report submitted for approval
# ---------------------------------------------------------------------------


def serialize_by_treatment(by_treatment: dict) -> dict:
    """JSON-safe view of the {treatment: {sale/purchase: {count, amount, vat}}}
    breakdown produced by provision.py."""
    return {
        treatment: {
            side: {
                "count": cell["count"],
                "amount": str(cell["amount"]),
                "vat": str(cell["vat"]),
            }
            for side, cell in sides.items()
        }
        for treatment, sides in by_treatment.items()
    }


@dataclass(frozen=True)
class VatProvisionReport:
    source_system: str
    generated_at: str
    currency: str
    period_label: str
    date_range: dict                  # {"from": iso, "to": iso}
    policy: dict
    computed_transactions: list
    by_treatment: dict                # {treatment: {sale/purchase: {count, amount, vat}}} (Decimal)
    output_vat_total: Decimal
    input_vat_total: Decimal
    net_vat: Decimal
    position: str
    anomalies: list
    narrative: Optional[FilingSupportNarrative]
    narrative_skipped_reason: Optional[str]
    model: Optional[str]
    narrative_prompt_hash: Optional[str]
    narrative_chunk_ids: list
    narrative_citations: list

    def summary(self) -> dict:
        excluded = [ct.transaction_id for ct in self.computed_transactions if not ct.included_in_totals]
        by_code: dict = {}
        for a in self.anomalies:
            by_code[a.code] = by_code.get(a.code, 0) + 1
        return {
            "transaction_count": len(self.computed_transactions),
            "period_label": self.period_label,
            "date_range": self.date_range,
            "output_vat_total": str(self.output_vat_total),
            "input_vat_total": str(self.input_vat_total),
            "net_vat": str(self.net_vat),
            "position": self.position,
            "by_treatment": serialize_by_treatment(self.by_treatment),
            "anomaly_count": len(self.anomalies),
            "anomalies_by_code": by_code,
            "anomalies": [a.to_dict() for a in self.anomalies],
            "transactions_excluded_from_totals": excluded,
        }

    def to_dict(self) -> dict:
        return {
            "source_system": self.source_system,
            "generated_at": self.generated_at,
            "currency": self.currency,
            "period_label": self.period_label,
            "date_range": self.date_range,
            "policy": self.policy,
            "summary": self.summary(),
            "computed_transactions": [ct.to_dict() for ct in self.computed_transactions],
            "by_treatment": serialize_by_treatment(self.by_treatment),
            "output_vat_total": str(self.output_vat_total),
            "input_vat_total": str(self.input_vat_total),
            "net_vat": str(self.net_vat),
            "position": self.position,
            "anomalies": [a.to_dict() for a in self.anomalies],
            "narrative": self.narrative.to_dict() if self.narrative is not None else None,
            "narrative_skipped_reason": self.narrative_skipped_reason,
            "model": self.model,
            "narrative_prompt_hash": self.narrative_prompt_hash,
            "narrative_chunk_ids": list(self.narrative_chunk_ids),
            "narrative_citations": list(self.narrative_citations),
        }
