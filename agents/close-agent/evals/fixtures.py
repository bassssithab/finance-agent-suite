"""Synthetic fixtures for the close-agent eval suite.

Nothing here is real: "Larenthia Trading Co" and its close policies are
invented for this test corpus (same fictional jurisdiction as
agents/vat-treatment-agent's synthetic VAT code and agents/ap-agent's synthetic
chart of accounts), not modeled on any real company, policy manual, or
accounting standard. The budget/actuals CSVs under fixtures/{budget,actuals}/
are fictional figures chosen to exercise the threshold rules — a mix of small
and large variances, zero-budget spend, and a missing actual.
"""

from knowledge import Document

BUDGET_DIR = "budget"
ACTUALS_DIR = "actuals"


# --- synthetic accounting-policy corpus --------------------------------------

VARIANCE_REVIEW_POLICY = Document(
    doc_id="doc-close-variance-policy",
    title="Larenthia Trading Co - Budget Variance Review Policy (Synthetic Fixture)",
    corpus="accounting_policy",
    metadata={"framework": "synthetic", "entity": "larenthia-trading", "period": "FY2026"},
    # Kept under platform/knowledge's 800-char chunk size so the whole policy
    # lands in a single chunk (position 0) — see POLICY_CITATION below.
    text="""\
Placeholder text for testing variance review, not a real close policy.

During the monthly close, the FP&A team computes the budget-to-actual variance \
for every general-ledger line. A line whose actual differs from budget by more \
than 10 percent, or by more than 25,000 dollars, requires a written variance \
explanation in the close file before the close is signed off.

A variance explanation should name the operational driver (volume, price, \
timing, or one-off items), state whether the driver is expected to persist \
into future periods, and cross-reference any related journal entry. Spend \
against a line with no approved budget must be explained and escalated to the \
budget owner regardless of amount.
""",
)

OPEX_CLASSIFICATION_NOTE = Document(
    doc_id="doc-close-opex-note",
    title="Larenthia Trading Co - Operating Expense Notes (Synthetic Fixture)",
    corpus="accounting_policy",
    metadata={"framework": "synthetic", "entity": "larenthia-trading", "period": "FY2026"},
    text="""\
Placeholder text for testing retrieval, not real accounting guidance.

Marketing paid-media spend is booked to account 6000 and is expected to track \
the campaign calendar; large positive variances usually reflect brought-forward \
campaign launches rather than rate changes. Contract labour (account 6200) is \
demand-driven and under-spends when project starts slip. Regulatory and \
professional fees (account 6900) are engagement-based and are frequently \
unbudgeted when a new regulatory matter arises mid-year.
""",
)

ALL_DOCUMENTS = [VARIANCE_REVIEW_POLICY, OPEX_CLASSIFICATION_NOTE]

# Citation label the KnowledgeBase produces for the (single) chunk of the
# variance-review policy above. chunk_document packs the whole doc into one
# chunk (position 0).
POLICY_CITATION = (
    "Larenthia Trading Co - Budget Variance Review Policy (Synthetic Fixture) "
    "(accounting_policy), chunk 0"
)
OPEX_NOTE_CITATION = (
    "Larenthia Trading Co - Operating Expense Notes (Synthetic Fixture) "
    "(accounting_policy), chunk 0"
)


# --- canned record_variance_explanations payloads (what the model would return) --


def explanation_entry(account: str, line_item: str, citation: str | None = None) -> dict:
    return {
        "account": account,
        "line_item": line_item,
        "explanation": (
            f"The variance on {line_item} is attributed to timing of activity in the period; "
            "a definitive driver needs confirmation from the budget owner."
            + (f" See {citation}." if citation else "")
        ),
        "citations": [citation] if citation else [],
        "primary_drivers": ["timing"],
    }


def explanations_payload(flagged_keys: list[tuple[str, str]], citation: str | None = POLICY_CITATION) -> dict:
    return {
        "explanations": [
            explanation_entry(account, line_item, citation)
            for account, line_item in flagged_keys
        ]
    }
