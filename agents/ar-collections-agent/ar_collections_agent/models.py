"""Data model for the ar-collections-agent aging + dunning workflow: the
flagging policy, one aged invoice, one drafted dunning email, and the
collections report that goes to approvals.

A `CollectionsReport` is never a final collections position by itself —
CLAUDE.md rule #2 requires it to go through `platform/approvals` before
anything is acted on (an email sent, an account put on hold). This module only
shapes the data and its audit/approval payloads; the deterministic aging and
flagging live in `aging.py` (plain code, rule #4), the dunning-email drafting in
`draft.py` (the only LLM call), orchestration in `runner.py`.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

# ---------------------------------------------------------------------------
# Aging buckets — fixed. Boundary convention (documented in README.md):
#   current : days_overdue <= 0
#   1-30    : 1..30
#   31-60   : 31..60
#   61-90   : 61..90   (inclusive)
#   90+     : >= 91     (i.e. strictly more than 90 days past due)
# ---------------------------------------------------------------------------

BUCKET_CURRENT = "current"
BUCKET_1_30 = "1-30"
BUCKET_31_60 = "31-60"
BUCKET_61_90 = "61-90"
BUCKET_90_PLUS = "90+"
BUCKETS = (BUCKET_CURRENT, BUCKET_1_30, BUCKET_31_60, BUCKET_61_90, BUCKET_90_PLUS)

# ---------------------------------------------------------------------------
# Dunning tone tiers — assigned deterministically from days_overdue in
# aging.py. The LLM writes *to* the assigned tier; it never picks the tier.
#   reminder : days_overdue <= 60 (gentle) — also repeat-customer 1-30 invoices
#   firm     : 61..90
#   formal   : >= 91
# ---------------------------------------------------------------------------

TONE_REMINDER = "reminder"
TONE_FIRM = "firm"
TONE_FORMAL = "formal"
TONE_TIERS = (TONE_REMINDER, TONE_FIRM, TONE_FORMAL)


# ---------------------------------------------------------------------------
# Flagging policy (consumed by aging.py) — all configurable per run
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DunningPolicy:
    """When an aged invoice warrants a drafted collection action.

    `min_days_overdue` flags any single invoice at or past that many days
    overdue. `flag_repeat_customers` additionally flags *every* overdue
    (`days_overdue >= 1`) invoice of a customer who has at least
    `repeat_customer_min_overdue_invoices` overdue invoices in the batch — so a
    customer who is slipping across several invoices gets chased even on the
    ones inside `min_days_overdue`. `min_amount`, when set, suppresses flagging
    for invoices whose open balance is below it (not worth a dunning email).
    """

    min_days_overdue: int = 31
    flag_repeat_customers: bool = True
    repeat_customer_min_overdue_invoices: int = 2
    min_amount: Optional[Decimal] = None

    def to_dict(self) -> dict:
        return {
            "min_days_overdue": self.min_days_overdue,
            "flag_repeat_customers": self.flag_repeat_customers,
            "repeat_customer_min_overdue_invoices": self.repeat_customer_min_overdue_invoices,
            "min_amount": str(self.min_amount) if self.min_amount is not None else None,
        }


# ---------------------------------------------------------------------------
# One aged invoice (produced by aging.py) — pure arithmetic, no LLM
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvoiceAging:
    invoice_id: str
    customer: str
    invoice_date: str            # ISO date, as normalized by the connector
    due_date: str                # ISO date
    amount: Decimal
    currency: str
    last_payment_date: Optional[str]      # ISO date or None
    days_overdue: int                     # as_of_date - due_date, in days (may be <= 0)
    days_since_last_payment: Optional[int]
    bucket: str                           # one of BUCKETS
    flagged: bool
    flag_reasons: list[str]
    tone_tier: Optional[str]              # one of TONE_TIERS; None when not flagged

    def to_dict(self) -> dict:
        return {
            "invoice_id": self.invoice_id,
            "customer": self.customer,
            "invoice_date": self.invoice_date,
            "due_date": self.due_date,
            "amount": str(self.amount),
            "currency": self.currency,
            "last_payment_date": self.last_payment_date,
            "days_overdue": self.days_overdue,
            "days_since_last_payment": self.days_since_last_payment,
            "bucket": self.bucket,
            "flagged": self.flagged,
            "flag_reasons": list(self.flag_reasons),
            "tone_tier": self.tone_tier,
        }


# ---------------------------------------------------------------------------
# One drafted dunning email (produced by draft.py from the model's tool call)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DunningDraft:
    invoice_id: str
    tone: str
    subject: str
    body: str
    citations: list[str]          # citation labels relied on; [] when ungrounded

    def to_dict(self) -> dict:
        return {
            "invoice_id": self.invoice_id,
            "tone": self.tone,
            "subject": self.subject,
            "body": self.body,
            "citations": list(self.citations),
        }


# ---------------------------------------------------------------------------
# The collections report submitted for approval
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CollectionsReport:
    as_of_date: str
    source_system: str
    generated_at: str
    currency: str
    policy: dict
    invoice_agings: list[InvoiceAging]     # every open invoice
    drafts: list[DunningDraft]             # flagged invoices only; [] if skipped
    drafts_skipped_reason: Optional[str]   # None if drafting ran and parsed
    model: Optional[str]
    draft_prompt_hash: Optional[str]
    draft_chunk_ids: list[str]
    draft_citations: list[str]

    @property
    def flagged(self) -> list[InvoiceAging]:
        return [ia for ia in self.invoice_agings if ia.flagged]

    def summary(self) -> dict:
        total_open = sum((ia.amount for ia in self.invoice_agings), Decimal("0"))
        overdue = [ia for ia in self.invoice_agings if ia.days_overdue >= 1]
        total_overdue = sum((ia.amount for ia in overdue), Decimal("0"))

        bucket_breakdown: dict[str, dict] = {
            bucket: {"count": 0, "amount": Decimal("0")} for bucket in BUCKETS
        }
        for ia in self.invoice_agings:
            entry = bucket_breakdown[ia.bucket]
            entry["count"] += 1
            entry["amount"] += ia.amount

        tone_breakdown: dict[str, int] = {}
        for ia in self.flagged:
            tone_breakdown[ia.tone_tier] = tone_breakdown.get(ia.tone_tier, 0) + 1

        return {
            "invoice_count": len(self.invoice_agings),
            "flagged_count": len(self.flagged),
            "total_open": str(total_open),
            "overdue_count": len(overdue),
            "total_overdue": str(total_overdue),
            "bucket_breakdown": {
                bucket: {"count": v["count"], "amount": str(v["amount"])}
                for bucket, v in bucket_breakdown.items()
            },
            "tone_breakdown": tone_breakdown,
            "customers_flagged": sorted({ia.customer for ia in self.flagged}),
        }

    def to_dict(self) -> dict:
        return {
            "as_of_date": self.as_of_date,
            "source_system": self.source_system,
            "generated_at": self.generated_at,
            "currency": self.currency,
            "policy": self.policy,
            "summary": self.summary(),
            "invoice_agings": [ia.to_dict() for ia in self.invoice_agings],
            "drafts": [d.to_dict() for d in self.drafts],
            "drafts_skipped_reason": self.drafts_skipped_reason,
            "model": self.model,
            "draft_prompt_hash": self.draft_prompt_hash,
            "draft_chunk_ids": list(self.draft_chunk_ids),
            "draft_citations": list(self.draft_citations),
        }
