"""Data model for a drafted AP invoice: what the vision step extracted, what
the deterministic check found, and what GL coding was suggested.

An `InvoiceDraft` is never a final AP record by itself — CLAUDE.md rule #2
requires it to go through `platform/approvals` before anything is considered
final. This module only shapes the data and its audit/approval payloads;
extraction happens in `extraction.py`, the arithmetic check in `sanity.py`,
coding in `coding.py`, orchestration in `runner.py`.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

# ---------------------------------------------------------------------------
# Extraction result (populated from the model's `record_invoice` tool call)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvoiceLineItem:
    description: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal


@dataclass(frozen=True)
class ExtractedInvoice:
    vendor_name: str
    invoice_number: str
    invoice_date: str          # as transcribed; normalization is out of scope
    currency: str
    line_items: list[InvoiceLineItem]
    grand_total: Decimal
    extraction_confidence: float  # 0.0–1.0, model self-reported

    def line_items_as_dicts(self) -> list[dict]:
        return [
            {
                "description": li.description,
                "quantity": str(li.quantity),
                "unit_price": str(li.unit_price),
                "line_total": str(li.line_total),
            }
            for li in self.line_items
        ]

    def to_dict(self) -> dict:
        return {
            "vendor_name": self.vendor_name,
            "invoice_number": self.invoice_number,
            "invoice_date": self.invoice_date,
            "currency": self.currency,
            "line_items": self.line_items_as_dicts(),
            "grand_total": str(self.grand_total),
            "extraction_confidence": self.extraction_confidence,
        }


# ---------------------------------------------------------------------------
# Deterministic sanity check (sanity.py) — plain arithmetic, no LLM
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LineTotalIssue:
    """A line where quantity * unit_price doesn't match the stated line_total."""

    line_index: int
    description: str
    quantity: Decimal
    unit_price: Decimal
    stated_line_total: Decimal
    computed_line_total: Decimal
    difference: Decimal  # computed - stated

    def to_dict(self) -> dict:
        return {
            "line_index": self.line_index,
            "description": self.description,
            "quantity": str(self.quantity),
            "unit_price": str(self.unit_price),
            "stated_line_total": str(self.stated_line_total),
            "computed_line_total": str(self.computed_line_total),
            "difference": str(self.difference),
        }


@dataclass(frozen=True)
class SanityCheckResult:
    ok: bool
    computed_line_sum: Decimal
    stated_grand_total: Decimal
    difference: Decimal            # computed_line_sum - stated_grand_total
    line_total_issues: list[LineTotalIssue]

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "computed_line_sum": str(self.computed_line_sum),
            "stated_grand_total": str(self.stated_grand_total),
            "difference": str(self.difference),
            "totals_reconcile": self.difference == 0,
            "line_total_issues": [i.to_dict() for i in self.line_total_issues],
        }


# ---------------------------------------------------------------------------
# GL coding suggestion (coding.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GLCodingSuggestion:
    line_index: int
    description: str
    account_code: Optional[str]   # None when the chart of accounts doesn't cover it
    account_name: Optional[str]
    rationale: str
    citation: Optional[str]       # citation label of the chunk relied on

    def to_dict(self) -> dict:
        return {
            "line_index": self.line_index,
            "description": self.description,
            "account_code": self.account_code,
            "account_name": self.account_name,
            "rationale": self.rationale,
            "citation": self.citation,
        }


# ---------------------------------------------------------------------------
# The drafted invoice submitted for approval
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvoiceDraft:
    invoice: ExtractedInvoice
    sanity_check: SanityCheckResult
    discrepancy_flagged: bool
    gl_suggestions: list[GLCodingSuggestion]
    coding_skipped_reason: Optional[str]  # None if coding ran; a reason string otherwise
    model: str
    extraction_prompt_hash: str
    coding_prompt_hash: Optional[str]
    coding_chunk_ids: list[str]
    coding_citations: list[str]
    source_document: dict  # {document_id, filename, media_type, sha256, size_bytes}

    def to_dict(self) -> dict:
        return {
            "invoice": self.invoice.to_dict(),
            "sanity_check": self.sanity_check.to_dict(),
            "discrepancy_flagged": self.discrepancy_flagged,
            "gl_suggestions": [s.to_dict() for s in self.gl_suggestions],
            "coding_skipped_reason": self.coding_skipped_reason,
            "model": self.model,
            "extraction_prompt_hash": self.extraction_prompt_hash,
            "coding_prompt_hash": self.coding_prompt_hash,
            "coding_chunk_ids": self.coding_chunk_ids,
            "coding_citations": self.coding_citations,
            "source_document": self.source_document,
        }
