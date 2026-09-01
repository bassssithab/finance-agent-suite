"""Synthetic fixtures for the expense-agent eval suite.

Nothing here is real: "Larenthia Trading Co" and its travel-and-expense policy
are invented for this test corpus (the same fictional entity as agents/ap-agent,
agents/close-agent and agents/vat-treatment-agent), not modeled on any real
company or policy manual. The receipt images under fixtures/receipts/ are
fictional rows chosen to exercise the compliance rules — a compliant taxi fare,
a meal over the per-meal limit, and a hotel bill that is months old.

All fixtures are checked against a fixed AS_OF date so the receipt-age rule is
stable. `load_expected(slug)` reads the committed ground truth that
generate_sample_receipts.py wrote next to each PNG; `record_receipt_payload`
returns it in the exact shape the model's forced tool call would produce, so a
test can hand it straight to fakes.receipt_client(...).
"""

import json
from datetime import date
from pathlib import Path

from knowledge import Document

RECEIPT_DIR = Path(__file__).parent / "fixtures" / "receipts"

# Every eval run checks the fixtures against this date.
AS_OF = date(2026, 9, 1)


def load_expected(slug: str) -> dict:
    return json.loads((RECEIPT_DIR / f"{slug}.expected.json").read_text())


# The model's record_receipt tool input == the expected.json shape as authored.
record_receipt_payload = load_expected


# --- synthetic expense-policy corpus -------------------------------------

EXPENSE_POLICY = Document(
    doc_id="doc-expense-policy-larenthia-trading",
    title="Larenthia Trading Co - Travel & Expense Policy (Synthetic Fixture)",
    corpus="expense_policy",
    metadata={"framework": "synthetic", "entity": "larenthia-trading", "period": "FY2026"},
    # Kept under platform/knowledge's 800-char chunk size so the whole policy
    # lands in a single chunk (position 0) — see POLICY_CITATION below.
    text="""\
Placeholder text for testing expense compliance, not a real T&E policy.

Meals are reimbursed up to 75 dollars per person per meal. Lodging is \
reimbursed up to 250 dollars per night. Local transport such as taxis and \
ride-hailing is reimbursed up to 60 dollars per trip. Every claim must be \
supported by an itemised receipt showing the merchant, the date, and the \
amount. A receipt must be submitted within 90 days of the expense date; older \
receipts are not reimbursed without a written exception from the budget owner. \
An expense that exceeds a category limit may still be reimbursed but requires \
the claimant's manager to approve the overage in writing before payment.
""",
)

RECEIPT_RULES_NOTE = Document(
    doc_id="doc-expense-receipt-rules-note",
    title="Larenthia Trading Co - Receipt Submission Notes (Synthetic Fixture)",
    corpus="expense_policy",
    metadata={"framework": "synthetic", "entity": "larenthia-trading", "period": "FY2026"},
    text="""\
Placeholder text for testing retrieval, not real expense guidance.

Receipts are uploaded to the expense tool as a photo or scan. A claim missing \
the merchant name, the transaction date, or a legible total is returned to the \
claimant rather than approved. Category is assigned by the reviewer from the \
merchant and the itemisation; the claimant's own category label is a starting \
point, not binding. The 90-day submission window runs from the date printed on \
the receipt, not the date of upload.
""",
)

ALL_DOCUMENTS = [EXPENSE_POLICY, RECEIPT_RULES_NOTE]

# Citation label the KnowledgeBase produces for the (single) chunk of the policy
# above. chunk_document packs the whole doc into one chunk (position 0).
POLICY_CITATION = (
    "Larenthia Trading Co - Travel & Expense Policy (Synthetic Fixture) "
    "(expense_policy), chunk 0"
)
RECEIPT_NOTE_CITATION = (
    "Larenthia Trading Co - Receipt Submission Notes (Synthetic Fixture) "
    "(expense_policy), chunk 0"
)


# --- canned record_policy_explanations payloads (what the model would return) --


def explanation_entry(code: str, citation: str | None = None) -> dict:
    return {
        "code": code,
        "explanation": (
            f"The receipt was flagged for '{code}'. The written expense policy on "
            "file addresses this, and the claim does not meet that requirement."
            + (f" See {citation}." if citation else " The policy on file does not cover this point.")
        ),
        "citations": [citation] if citation else [],
    }


def explanations_payload(
    codes: list[str], citation: str | None = POLICY_CITATION
) -> dict:
    return {"explanations": [explanation_entry(code, citation) for code in codes]}
