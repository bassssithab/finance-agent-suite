"""Normalized record shapes all connectors return. See docs/ARCHITECTURE.md."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class Transaction:
    """One normalized transaction, regardless of which system it came from.

    `raw` keeps the original source row so a human or agent can always trace
    a normalized record back to the exact bank/ledger line it came from.
    """

    source_system: str
    source_capability: str
    account: str
    date: date
    amount: Decimal
    currency: str
    description: str
    reference: Optional[str]
    raw: dict[str, str]


@dataclass(frozen=True)
class BudgetActualLine:
    """One normalized planning/actuals line for a single period.

    A budget export and an actuals export share this shape; `source_capability`
    ("budget" or "actuals") is the only thing that distinguishes them, the same
    way `Transaction.source_capability` distinguishes a bank line from a ledger
    line. `raw` keeps the original source row so a human or agent can trace a
    normalized record back to the exact CSV line it came from.
    """

    source_system: str
    source_capability: str  # "budget" | "actuals"
    period: str             # as given, e.g. "2026-07" — no normalization
    account: str            # GL account code, e.g. "6000"
    line_item: str          # human label, e.g. "Marketing — paid media"
    category: str            # optional grouping, e.g. "Operating expenses"; "" if absent
    amount: Decimal
    currency: str
    raw: dict[str, str]


@dataclass(frozen=True)
class OpenInvoice:
    """One normalized open (unpaid or partly-paid) accounts-receivable invoice,
    as exported from an ERP's AR sub-ledger.

    Carries just enough to age the invoice and draft a collection message:
    who owes it (`customer`), when it was raised and when it fell due, the
    open `amount`, and — when the source records it — the date of the last
    payment received against the account (`last_payment_date`, `None` when the
    source leaves it blank). This connector does not derive the open amount
    from cash applied (that is the ERP's job); an aging analysis only needs the
    balance still outstanding. `raw` keeps the original CSV row so a normalized
    record can always be traced back to the exact line it came from.
    """

    source_system: str
    source_capability: str  # always "open_invoices"
    invoice_id: str         # source's own id for the invoice, e.g. "INV-4102"
    customer: str           # customer name/id, as given
    invoice_date: date
    due_date: date
    amount: Decimal         # open balance as reported by the source
    currency: str
    last_payment_date: Optional[date]
    raw: dict[str, str]


@dataclass(frozen=True)
class JournalEntry:
    """One normalized journal-entry header, as exported from an ERP's GL.

    Carries just enough to test approval controls over the entry: who prepared
    it and who approved it (`approver_1`, and `approver_2` for entries that went
    through a second sign-off). `amount` is the entry's absolute magnitude as
    reported by the source — this connector does not derive it from debit/credit
    legs (that is `Transaction`'s job); a controls test only needs the size of
    the entry to decide whether dual approval was required.

    `approver_1` / `approver_2` are `None` when the source leaves them blank (an
    unapproved or singly-approved entry) — the control test, not the connector,
    decides whether that is a violation. `raw` keeps the original CSV row so a
    normalized record can always be traced back to the exact line it came from.
    """

    source_system: str
    source_capability: str  # always "journal_entries"
    entry_id: str           # source's own id for the entry, e.g. "JE-1042"
    date: date
    account: str            # GL account the entry posts to, as given
    amount: Decimal         # entry magnitude as reported by the source
    currency: str
    preparer: str
    approver_1: Optional[str]
    approver_2: Optional[str]
    raw: dict[str, str]


@dataclass(frozen=True)
class VatTransaction:
    """One normalized transaction line from a period's VAT transaction export.

    Carries just enough to build a period-end VAT provision: whether it is a
    sale or a purchase, the net amount, the VAT treatment the source recorded
    for it, and the VAT rate where one applies. `transaction_type` and
    `vat_treatment` are kept exactly as the source gave them (lower-casing,
    canonicalisation and validation against the recognised categories are the
    agent's job, not the connector's — same stance as `JournalEntry`'s
    approver fields).

    `vat_rate` is `None` when the source leaves it blank — deliberately distinct
    from `Decimal("0")`, so the agent can tell "no rate recorded" (a possible
    data-quality problem on a standard-rated line) apart from "zero-rated". `raw`
    keeps the original CSV row so a normalized record can always be traced back
    to the exact line it came from.
    """

    source_system: str
    source_capability: str  # always "vat_transactions"
    transaction_id: str
    date: date
    transaction_type: str   # as given, e.g. "sale" / "purchase"
    amount: Decimal         # net amount as reported by the source
    vat_treatment: str      # as given; "" when the source leaves it blank
    vat_rate: Optional[Decimal]  # None when blank; distinct from Decimal("0")
    currency: str
    raw: dict[str, str]


@dataclass(frozen=True)
class InternalControl:
    """One row from the company's internal-controls register, as exported from a
    GRC tool or a controls spreadsheet.

    Carries just enough to triage a control against a regulatory requirement:
    its id, a free-text description of what the control does, and an optional
    grouping `category`. `description` and `category` are kept exactly as the
    source gave them — tokenising and relevance-matching them is the agent's
    job, not the connector's. `raw` keeps the original CSV row so a normalized
    record can always be traced back to the exact line it came from.
    """

    source_system: str
    source_capability: str  # always "internal_controls"
    control_id: str
    description: str
    category: str            # "" when the source leaves it blank
    raw: dict[str, str]


@dataclass(frozen=True)
class SourceDocument:
    """One binary source document (a scanned invoice, receipt, contract PDF)
    handed to an agent by a document connector.

    Read-first, same contract as `Transaction`: the connector returns the raw
    bytes plus enough identity to trace and cite the document, and nothing
    else. `sha256` is over `content` — an agent records it in its own
    `AuditEvent` so the exact file a run acted on can always be identified,
    without copying the bytes into the audit log.
    """

    source_system: str
    source_capability: str  # always "documents"
    document_id: str        # stable id within the connector (here: the filename)
    filename: str
    media_type: str         # e.g. "image/png", "image/jpeg", "application/pdf"
    content: bytes
    size_bytes: int
    sha256: str
