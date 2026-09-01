"""Synthetic fixtures for the ar-collections-agent eval suite.

Nothing here is real: "Larenthia Trading Co" and its collections policy are
invented for this test corpus (the same fictional entity as agents/close-agent,
agents/controls-sox-agent and agents/ap-agent), not modeled on any real company,
policy manual, or regulation. The open-invoice CSVs under
fixtures/open_invoices/ are fictional rows chosen to exercise the aging buckets
and the flagging rules — a current book, a mixed book (a 31-60 invoice, a 61-90
invoice, and a repeat-offender customer), and a severely delinquent book (90+
invoices and a customer with three overdue invoices).

All fixtures are aged against a fixed AS_OF date so the buckets are stable.
"""

from datetime import date

from knowledge import Document

OPEN_INVOICES_DIR = "open_invoices"

# Every eval run ages the fixtures against this date.
AS_OF = date(2026, 9, 1)


# --- synthetic collections-policy corpus -----------------------------------

COLLECTIONS_POLICY = Document(
    doc_id="doc-ar-collections-policy",
    title="Larenthia Trading Co - Accounts Receivable Collections Policy (Synthetic Fixture)",
    corpus="collections_policy",
    metadata={"framework": "synthetic", "entity": "larenthia-trading", "period": "FY2026"},
    # Kept under platform/knowledge's 800-char chunk size so the whole policy
    # lands in a single chunk (position 0) — see POLICY_CITATION below.
    text="""\
Placeholder text for testing collections dunning, not a real collections policy.

Standard customer payment terms are net 30 days from the invoice date. An \
invoice is followed up once it is 31 days or more past due, and any customer \
with two or more overdue invoices is followed up on the whole account. The \
first reminder is courteous, a second notice at 61 days is firm, and a final \
notice at 91 days states that the account is being escalated for review. \
Collections staff do not offer discounts, waive balances, or agree payment \
plans on the call; those are routed to the credit manager. Larenthia does not \
charge late-payment interest or fees on trade receivables.
""",
)

CONTACT_NOTE = Document(
    doc_id="doc-ar-contact-note",
    title="Larenthia Trading Co - Customer Contact Notes (Synthetic Fixture)",
    corpus="collections_policy",
    metadata={"framework": "synthetic", "entity": "larenthia-trading", "period": "FY2026"},
    text="""\
Placeholder text for testing retrieval, not real collections guidance.

Dunning emails go to the customer's accounts-payable inbox with the invoice \
number in the subject line. Keep the message short: state the invoice, the \
amount, the due date and how many days it is overdue, and ask for a payment \
date. Where a customer has recently paid other invoices, acknowledge it. \
Escalation past the final notice is decided by the credit manager, not by \
collections staff.
""",
)

ALL_DOCUMENTS = [COLLECTIONS_POLICY, CONTACT_NOTE]

# Citation label the KnowledgeBase produces for the (single) chunk of the
# collections policy above. chunk_document packs the whole doc into one chunk
# (position 0).
POLICY_CITATION = (
    "Larenthia Trading Co - Accounts Receivable Collections Policy (Synthetic Fixture) "
    "(collections_policy), chunk 0"
)
CONTACT_NOTE_CITATION = (
    "Larenthia Trading Co - Customer Contact Notes (Synthetic Fixture) "
    "(collections_policy), chunk 0"
)


# --- canned record_dunning_drafts payloads (what the model would return) ---


def dunning_entry(invoice_id: str, tone: str, citation: str | None = None) -> dict:
    return {
        "invoice_id": invoice_id,
        "tone": tone,
        "subject": f"Overdue invoice {invoice_id}",
        "body": (
            f"Hello,\n\nOur records show invoice {invoice_id} is past its due date. "
            "Please let us know when we can expect payment.\n\nThank you."
            + (f"\n\n[{citation}]" if citation else "")
        ),
        "citations": [citation] if citation else [],
    }


def dunning_payload(
    flagged_keys: list[tuple[str, str]], citation: str | None = POLICY_CITATION
) -> dict:
    """`flagged_keys` is a list of (invoice_id, tone_tier)."""
    return {
        "drafts": [
            dunning_entry(invoice_id, tone, citation)
            for invoice_id, tone in flagged_keys
        ]
    }
