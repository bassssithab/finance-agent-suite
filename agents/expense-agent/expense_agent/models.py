"""Data model for a drafted expense receipt: what the vision step extracted,
what the deterministic policy check found, and any cited explanation of the
flagged violations.

An `ExpenseDraft` is never a final T&E decision by itself — CLAUDE.md rule #2
requires it to go through `platform/approvals` before anything is acted on (a
reimbursement paid, a claim rejected). This module only shapes the data and its
audit/approval payloads; extraction happens in `extraction.py`, the
deterministic checks in `compliance.py` (plain code, rule #4), the cited
explanation in `explain.py` (the optional second LLM call), orchestration in
`runner.py`.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

# ---------------------------------------------------------------------------
# Extraction result (populated from the model's `record_receipt` tool call)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractedReceipt:
    vendor: str
    date: str                  # as transcribed; parsing/normalization is compliance.py's job
    amount: Decimal
    currency: str
    expense_category: str      # the model's best-guess label from the receipt
    extraction_confidence: float  # 0.0–1.0, model self-reported

    def to_dict(self) -> dict:
        return {
            "vendor": self.vendor,
            "date": self.date,
            "amount": str(self.amount),
            "currency": self.currency,
            "expense_category": self.expense_category,
            "extraction_confidence": self.extraction_confidence,
        }


# ---------------------------------------------------------------------------
# Expense policy (consumed by compliance.py) — all configurable per run
# ---------------------------------------------------------------------------


DEFAULT_REQUIRED_FIELDS = ("vendor", "date", "amount", "currency", "expense_category")
DEFAULT_DATE_FORMATS = ("%Y-%m-%d",)


@dataclass(frozen=True)
class ExpensePolicy:
    """The company-policy inputs to the deterministic compliance check.

    `category_limits` keys are matched against the extracted `expense_category`
    case- and surrounding-whitespace-insensitively, exact match only — a
    deliberate limitation (see README.md): a label the map does not contain
    falls back to `default_limit` (or is uncapped when that is None). `amount`
    is compared strictly greater than the limit, so a receipt exactly at the
    limit passes. `max_receipt_age_days` is likewise inclusive — a receipt
    exactly that many days old passes.
    """

    category_limits: dict = field(default_factory=dict)     # {category: Decimal}
    default_limit: Optional[Decimal] = None
    max_receipt_age_days: Optional[int] = None
    required_fields: tuple = DEFAULT_REQUIRED_FIELDS
    date_formats: tuple = DEFAULT_DATE_FORMATS

    def to_dict(self) -> dict:
        return {
            "category_limits": {k: str(v) for k, v in self.category_limits.items()},
            "default_limit": str(self.default_limit) if self.default_limit is not None else None,
            "max_receipt_age_days": self.max_receipt_age_days,
            "required_fields": list(self.required_fields),
            "date_formats": list(self.date_formats),
        }


# ---------------------------------------------------------------------------
# One policy violation (produced by compliance.py) — pure code, no LLM
# ---------------------------------------------------------------------------

# Violation codes, each at most once per receipt:
#   "missing_required_field" — a required field came back empty/blank
#   "date_unparseable"       — the receipt date matched no configured format
#   "category_over_limit"    — amount exceeds the limit for its category
#   "receipt_too_old"        — the receipt is older than the max age
VIOLATION_CODES = (
    "missing_required_field",
    "date_unparseable",
    "category_over_limit",
    "receipt_too_old",
)


@dataclass(frozen=True)
class Violation:
    code: str
    field: str    # the receipt field the violation concerns ("amount", "date", ...)
    detail: str   # deterministic human-readable reason, safe to drop into the audit log

    def to_dict(self) -> dict:
        return {"code": self.code, "field": self.field, "detail": self.detail}


@dataclass(frozen=True)
class ComplianceCheckResult:
    passed: bool
    violations: list  # list[Violation]
    parsed_date: Optional[str]      # ISO date the receipt date parsed to, or None
    applied_limit: Optional[Decimal]  # the category/default limit that applied, or None
    as_of_date: str                # ISO date the age check was run against

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "violations": [v.to_dict() for v in self.violations],
            "parsed_date": self.parsed_date,
            "applied_limit": str(self.applied_limit) if self.applied_limit is not None else None,
            "as_of_date": self.as_of_date,
        }


# ---------------------------------------------------------------------------
# One cited policy explanation (produced by explain.py from the tool call)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyExplanation:
    code: str                 # the violation code this explains
    explanation: str
    citations: list           # citation labels relied on; [] when ungrounded

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "explanation": self.explanation,
            "citations": list(self.citations),
        }


# ---------------------------------------------------------------------------
# The drafted expense submitted for approval
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExpenseDraft:
    receipt: ExtractedReceipt
    compliance: ComplianceCheckResult
    compliance_flagged: bool
    explanations: list                       # list[PolicyExplanation]; [] if skipped
    explanation_skipped_reason: Optional[str]  # None if drafting ran and parsed
    model: str
    extraction_prompt_hash: str
    explanation_prompt_hash: Optional[str]
    explanation_chunk_ids: list
    explanation_citations: list
    source_document: dict  # {document_id, filename, media_type, sha256, size_bytes}

    def to_dict(self) -> dict:
        return {
            "receipt": self.receipt.to_dict(),
            "compliance": self.compliance.to_dict(),
            "compliance_flagged": self.compliance_flagged,
            "explanations": [e.to_dict() for e in self.explanations],
            "explanation_skipped_reason": self.explanation_skipped_reason,
            "model": self.model,
            "extraction_prompt_hash": self.extraction_prompt_hash,
            "explanation_prompt_hash": self.explanation_prompt_hash,
            "explanation_chunk_ids": list(self.explanation_chunk_ids),
            "explanation_citations": list(self.explanation_citations),
            "source_document": self.source_document,
        }
